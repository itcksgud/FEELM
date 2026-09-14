from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/evaluate_service_v1_b1_r3.py"
EVALUATION_AUDITOR_PATH = (Path(__file__).resolve().parents[1]
                           / "scripts/audit_service_v1_b1_evaluation_outputs_r3.py")
SPEC = importlib.util.spec_from_file_location("evaluate_service_v1_b1_r3", MODULE_PATH)
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
    team_root = root.parent / "team" if root.name == "standalone" else root / "team"
    return ev.path_pin(path, root, team_root)


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
        self.base = base
        self.root = base / "standalone"
        self.score_rows = 28
        self.catalog_movies = 8
        self.users = list(range(11, 18))
        self.root.mkdir(parents=True, exist_ok=True)
        self.team_root = base / "team"
        self.plan = self._text("docs/recommendation/plans/service-v1-b1-r3-fit-recovery.md",
                               "reviewed synthetic B1 plan\n")
        self.contract = self._text("team/pipeline/docs/service-v1/EVALUATION.md",
                                   "synthetic evaluation contract\n")
        self.outer = self._text("scripts/run_service_v1_b1_gbt_r3.py", "# r3 outer\n")
        self.worker = self._text("scripts/service_v1_b1_spark_worker.py", "# worker\n")
        self.runner_test = self._text("tests/test_service_v1_b1_gbt_runner_r3.py", "# r3 tests\n")
        self.portable_reader = self._text("scripts/combination340_models.py", "# reader\n")
        self.portable_dependency = self._text("scripts/rec046_common.py", "# dependency\n")
        self.spark_auditor = self._text("scripts/audit_service_v1_b1_spark_outputs_r3.py",
                                        "# independent r3 auditor\n")
        self.r2_outer = self._text("scripts/run_service_v1_b1_gbt.py", "# r2 outer\n")
        self.r2_plan = self._text("docs/recommendation/plans/service-v1-b1-spark-runner.md",
                                  "reviewed synthetic r2 plan\n")

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
        self.artifact_manifest = self.team_root / "pipeline/artifacts/service-v1.json"
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

        self.profile = self.root / (
            "docs/recommendation/plans/service-v1-b1-r3-local4c12g-t14400-profile.json")

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
            "execution/service-v1-b1-r3-fit-recovery.md": self.plan,
            "execution/local4c12g-t14400-profile.json": self.profile,
            "execution/run_service_v1_b1_gbt_r3.py": self.outer,
            "implementation/service_v1_b1_spark_worker.py": self.worker,
            "execution/test_service_v1_b1_gbt_runner_r3.py": self.runner_test,
            "implementation/combination340_models.py": self.portable_reader,
            "implementation/rec046_common.py": self.portable_dependency,
            "source/natural-score.parquet": self.score_axis,
        }

        model_records_seed = [
            {"path": name, **ev.pin(source_paths[name])}
            for name in sorted(ev.MODEL_INPUT_PATHS)
        ]
        runtime_records_seed = [
            {"path": name, **ev.pin(source_paths[name])}
            for name in sorted(ev.WORKER_RUNTIME_PATHS - {"runtime/docker-image-id"})
        ]
        docker_raw = ev.IMAGE_ID.encode("utf-8")
        runtime_records_seed.append({
            "path": "runtime/docker-image-id", "bytes": len(docker_raw),
            "sha256": hashlib.sha256(docker_raw).hexdigest(), "value": ev.IMAGE_ID,
        })
        runtime_records_seed.sort(key=lambda record: record["path"])
        dump_json(self.profile, {
            "schemaVersion": "feelm-service-v1-b1-execution-profile/1",
            "status": "REVIEWED_LOCAL_BOUNDED_PROBE", "profileId": ev.PROFILE_ID,
            "runId": ev.RUN_ID, "recoveryOrdinal": "r3",
            "hostClass": "current-local-docker-desktop", "singleAttempt": True,
            "automaticRetry": False,
            "docker": {"cpus": "4", "memory": "12g", "memorySwap": "12g",
                       "maxMemoryBytes": 12 * 1024**3, "network": "none"},
            "spark": {"master": "local[4]", "driverMemory": "8g",
                      "shufflePartitions": 8, "adaptiveExecution": False},
            "timeoutsSeconds": {"preflightDryRun": 14400, "preflightFull": 14400,
                                "fit": 14400, "score": 14400},
            "resourceObservation": {},
            "modelContract": {
                "sourceRows": ev.TRAINING_ROWS, "logicalRows": 4 * ev.TRAINING_ROWS,
                "scoreRows": ev.SCORE_ROWS, "featureCount": 230, "partitions": 8,
                "seed": 339, "trees": 120,
                "modelInputSetSha256": ev.canonical_record_set_digest(model_records_seed),
                "workerRuntimeSetSha256": ev.canonical_record_set_digest(runtime_records_seed),
            },
            "authorization": {},
        })

        def source_record(logical: str) -> dict:
            if logical == "runtime/docker-image-id":
                raw = ev.IMAGE_ID.encode("utf-8")
                return {"path": logical, "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(), "value": ev.IMAGE_ID}
            return {"path": logical, **ev.pin(source_paths[logical])}

        model_sources = [source_record(name) for name in sorted(ev.MODEL_INPUT_PATHS)]
        runtime_sources = [source_record(name) for name in sorted(ev.WORKER_RUNTIME_PATHS)]
        execution_sources = [source_record(name) for name in sorted(ev.EXECUTION_PATHS)]
        fit_sources = [*model_sources, *runtime_sources, *execution_sources]
        score_sources = [source_record(name) for name in sorted(ev.SCORE_SOURCE_PATHS)]
        outer_alias = source_record("execution/run_service_v1_b1_gbt_r3.py")
        worker_alias = source_record("implementation/service_v1_b1_spark_worker.py")
        implementation_digest = ev.canonical_record_set_digest([outer_alias, worker_alias])
        fit_source_digest = ev.canonical_record_set_digest(fit_sources)

        output_parent = self.root / "outputs/recommendation-evidence/service-v1-pretraining-20260913"
        r2_training_digest = ev.canonical_record_set_digest(fit_sources)
        r2_dir = output_parent / (ev.R2_RUN_ID + "-preflight")
        r2_dir.mkdir(parents=True)
        r2_controls: list[dict] = []
        r2_control_digest = ev.canonical_record_set_digest(r2_controls)
        r2_input_digest = ev.canonical_record_set_digest(fit_sources)
        dump_json(r2_dir / "input-lock.json", {
            "schemaVersion": "feelm-service-v1-b1-input-lock/1", "phase": "preflight",
            "trainingSourceRecords": fit_sources, "controlReferences": r2_controls,
            "trainingSourceSetSha256": r2_training_digest,
            "controlReferenceSetSha256": r2_control_digest,
            "inputSetSha256": r2_input_digest, "evaluationTargetsRead": False,
        })
        dump_json(r2_dir / "recovery-reference.json", {"status": "SYNTHETIC_R2"})
        dump_json(r2_dir / "partition-identity.json", {"status": "PASS"})
        dump_json(r2_dir / "command.json", {"phase": "preflight"})
        dump_json(r2_dir / "resource.json", {"status": "PASS"})
        self._text_at(r2_dir / "run.log", "r2 preflight complete\n")
        r2_required = {"input-lock.json", "recovery-reference.json",
                       "partition-identity.json", "command.json", "resource.json", "run.log"}
        r2_manifest = r2_dir / "manifest.json"
        dump_json(r2_manifest, {
            "schemaVersion": "feelm-service-v1-b1-preflight-manifest/1",
            "runId": ev.R2_RUN_ID, "status": "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW",
            "sourceRows": ev.TRAINING_ROWS, "logicalRows": 4 * ev.TRAINING_ROWS,
            "partitionCount": 8, "resourceStatus": "PASS", "modelFitPerformed": False,
            "fitAuthorized": False, "trainingSourceSetSha256": r2_training_digest,
            "controlReferenceSetSha256": r2_control_digest,
            "inputSetSha256": r2_input_digest,
            "implementationSetSha256": implementation_digest,
            "files": {name: ev.path_pin(r2_dir / name, r2_dir)
                      for name in sorted(r2_required)},
        })
        r2_inventory = [root_pin(path, self.root) for path in sorted(
            (item for item in r2_dir.iterdir() if item.is_file()), key=lambda item: item.name)]
        r2_review = output_parent / (ev.R2_RUN_ID + "-preflight-result-review.json")
        dump_json(r2_review, {
            "schemaVersion": "feelm-service-v1-b1-result-review/1", "phase": "preflight",
            "status": "PASS", "readyForService": False, "modelFitPerformed": False,
            "target": {"manifest": root_pin(r2_manifest, self.root),
                       "input_lock": root_pin(r2_dir / "input-lock.json", self.root)},
            "dependencyFingerprint": {
                "files": {},
                "bundleInventories": {self._logical(r2_dir): self._bundle_inventory(r2_dir)},
            },
        })
        r2_failure = output_parent / (ev.R2_RUN_ID + "-fit-failure.json")
        self.r2_failure = r2_failure
        dump_json(r2_failure, {
            "schemaVersion": "feelm-service-v1-b1-failure/1",
            "phase": "fit", "status": "FAILED", "cleanupComplete": True,
            "containerRun": {
                "dockerState": {"ExitCode": 143, "OOMKilled": False},
                "timedOut": True, "workerTerminalResultPresent": False,
                "resource": {"resourceStatus": "RESOURCE_STOP", "timedOut": True},
            },
        })
        r2_plan_record = {
            "path": "ancestor/service-v1-b1-spark-runner.md",
            **ev.pin(self.r2_plan),
        }
        r2_controls = [*r2_inventory, root_pin(r2_review, self.root),
                       root_pin(r2_failure, self.root), root_pin(self.r2_outer, self.root),
                       r2_plan_record]

        self.preflight_dir = output_parent / (ev.RUN_ID + "-preflight")
        self.preflight_dir.mkdir(parents=True)
        preflight_lock = self._group_lock(
            "preflight", model_sources, runtime_sources, execution_sources,
            r2_controls, r2_training_digest)
        dump_json(self.preflight_dir / "input-lock.json", preflight_lock)
        profile_record = source_record("execution/local4c12g-t14400-profile.json")
        recovery = {
            "schemaVersion": "feelm-service-v1-b1-r3-recovery-reference/1",
            "status": "R2_ANCESTRY_VERIFIED", "runId": ev.RUN_ID,
            "profileId": ev.PROFILE_ID, "r2RunId": ev.R2_RUN_ID,
            "r2PreflightManifest": root_pin(r2_manifest, self.root),
            "r2PreflightReview": root_pin(r2_review, self.root),
            "r2PreflightBundleInventory": r2_inventory,
            "r2PreflightDigests": {
                "trainingSourceSetSha256": r2_training_digest,
                "controlReferenceSetSha256": r2_control_digest,
                "inputSetSha256": r2_input_digest,
                "implementationSetSha256": implementation_digest,
            },
            "r2FitFailure": root_pin(r2_failure, self.root),
            "r2FitFailureFacts": {
                "phase": "fit", "status": "FAILED", "timedOut": True,
                "resourceStatus": "RESOURCE_STOP", "exitCode": 143,
                "oomKilled": False, "cleanupComplete": True,
                "workerTerminalResultPresent": False, "modelWritten": False,
            },
            "r2OuterRunner": root_pin(self.r2_outer, self.root),
            "r2Plan": r2_plan_record,
            "sparkWorker": worker_alias, "outerRunner": outer_alias,
            "executionProfile": profile_record,
            "digestComparison": {
                "r2HistoricalTrainingSourceSetSha256": r2_training_digest,
                "trainingRecipe": source_record("contract/training-recipe.v1.json"),
                "sourceRows": ev.TRAINING_ROWS, "logicalRows": 4 * ev.TRAINING_ROWS,
                "modelInputSetSha256": preflight_lock["modelInputSetSha256"],
                "workerRuntimeSetSha256": preflight_lock["workerRuntimeSetSha256"],
                "executionSetSha256": preflight_lock["executionSetSha256"],
                "modelInputUnchanged": True, "workerRuntimeUnchanged": True,
                "partitionCount": 8, "seed": 339,
            },
            "modelFitPerformedByPreflight": False, "deploymentAuthorized": False,
        }
        dump_json(self.preflight_dir / "recovery-reference.json", recovery)
        self._phase_profile(self.preflight_dir / "execution-profile.json", "preflight",
                            profile_record, outer_alias, preflight_lock["executionSetSha256"])
        dump_json(self.preflight_dir / "partition-identity.json", {"status": "PASS"})
        dump_json(self.preflight_dir / "command.json", {"phase": "preflight"})
        dump_json(self.preflight_dir / "resource.json", {"status": "PASS"})
        self._text_at(self.preflight_dir / "run.log", "preflight complete\n")
        preflight_required = {
            "input-lock.json", "recovery-reference.json", "partition-identity.json",
            "execution-profile.json", "command.json", "resource.json", "run.log",
        }
        self.preflight_manifest = self.preflight_dir / "manifest.json"
        dump_json(self.preflight_manifest, {
            "schemaVersion": "feelm-service-v1-b1-preflight-manifest/2",
            "runId": ev.RUN_ID, "profileId": ev.PROFILE_ID,
            "status": "B1_FULL_PREFLIGHT_COMPLETE_AWAITING_REVIEW",
            "fitAuthorized": False, "modelFitPerformed": False, "scorePerformed": False,
            "readyForService": False,
            "inputSetSha256": preflight_lock["inputSetSha256"],
            "modelInputSetSha256": preflight_lock["modelInputSetSha256"],
            "workerRuntimeSetSha256": preflight_lock["workerRuntimeSetSha256"],
            "executionSetSha256": preflight_lock["executionSetSha256"],
            "controlReferenceSetSha256": preflight_lock["controlReferenceSetSha256"],
            "r2TrainingSourceSetSha256": r2_training_digest,
            "r2PreflightManifestSha256": ev.sha256_file(r2_manifest),
            "r2PreflightReviewSha256": ev.sha256_file(r2_review),
            "r2FitFailureSha256": ev.sha256_file(r2_failure),
            "recoveryReferenceSha256": ev.sha256_file(
                self.preflight_dir / "recovery-reference.json"),
            "outerRunnerSha256": outer_alias["sha256"],
            "sparkWorkerSha256": worker_alias["sha256"],
            "implementationSetSha256": implementation_digest,
            "executionProfileSha256": ev.sha256_file(
                self.preflight_dir / "execution-profile.json"),
            "files": {name: ev.path_pin(self.preflight_dir / name, self.preflight_dir)
                      for name in sorted(preflight_required)},
        })
        self.preflight_review = output_parent / (ev.RUN_ID + "-preflight-result-review.json")
        preflight_target_paths = {
            "manifest": self.preflight_manifest,
            "input_lock": self.preflight_dir / "input-lock.json",
            "recovery_reference": self.preflight_dir / "recovery-reference.json",
            "execution_profile": self.preflight_dir / "execution-profile.json",
            "partition_identity": self.preflight_dir / "partition-identity.json",
            "command": self.preflight_dir / "command.json",
            "resource": self.preflight_dir / "resource.json",
            "run_log": self.preflight_dir / "run.log",
            "outer_runner": self.outer,
            "spark_worker": self.worker,
        }
        dependency_files = [source_paths[name] for name in sorted(
            ev.TRAINING_SOURCE_PATHS - {"runtime/docker-image-id"})] + [
                r2_review, r2_failure, self.r2_outer, self.r2_plan]
        self._spark_review(self.preflight_review, "preflight", preflight_target_paths,
                           preflight_lock, output_parent, dependency_files)

        preflight_controls = [root_pin(path, self.root) for path in sorted(
            (path for path in self.preflight_dir.rglob("*") if path.is_file()),
            key=lambda path: path.as_posix())]
        preflight_controls.append(root_pin(self.preflight_review, self.root))
        preflight_controls.extend(r2_controls)

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
        fit_lock = self._group_lock(
            "fit", model_sources, runtime_sources, execution_sources,
            preflight_controls, r2_training_digest)
        dump_json(self.fit_dir / "input-lock.json", fit_lock)
        dump_json(self.fit_dir / "recovery-reference.json", recovery)
        self._phase_profile(self.fit_dir / "execution-profile.json", "fit",
                            profile_record, outer_alias, fit_lock["executionSetSha256"])
        embedded_recovery = {**ev._canonical_json_pin(recovery), "payload": recovery}
        dump_json(self.fit_dir / "preflight-reference.json", {
            "schemaVersion": "feelm-service-v1-b1-preflight-reference/2",
            "runId": ev.RUN_ID, "profileId": ev.PROFILE_ID,
            "preflightManifest": root_pin(self.preflight_manifest, self.root),
            "preflightReview": root_pin(self.preflight_review, self.root),
            "reviewedArtifacts": preflight_controls,
            "recoveryReference": embedded_recovery,
            "outerRunner": outer_alias, "sparkWorker": worker_alias,
            "executionProfile": profile_record,
            "modelInputSetSha256": fit_lock["modelInputSetSha256"],
            "workerRuntimeSetSha256": fit_lock["workerRuntimeSetSha256"],
            "executionSetSha256": fit_lock["executionSetSha256"],
            "controlReferenceSetSha256": fit_lock["controlReferenceSetSha256"],
            "fitInputSetSha256": fit_lock["inputSetSha256"],
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
            "execution-profile.json", "recovery-reference.json",
            "model/native/data/part-00000",
        }
        dump_json(self.fit_manifest, {
            "schemaVersion": "feelm-service-v1-b1-fit-manifest/2",
            "runId": ev.RUN_ID, "profileId": ev.PROFILE_ID,
            "status": "B1_MODEL_FIT_COMPLETE_AUDIT_PENDING",
            "scoringAuthorized": False, "readyForService": False,
            "modelFitPerformed": True, "scorePerformed": False,
            "inputSetSha256": fit_lock["inputSetSha256"],
            "modelInputSetSha256": fit_lock["modelInputSetSha256"],
            "workerRuntimeSetSha256": fit_lock["workerRuntimeSetSha256"],
            "executionSetSha256": fit_lock["executionSetSha256"],
            "controlReferenceSetSha256": fit_lock["controlReferenceSetSha256"],
            "preflightManifestSha256": ev.sha256_file(self.preflight_manifest),
            "preflightReviewSha256": ev.sha256_file(self.preflight_review),
            "outerRunnerSha256": outer_alias["sha256"],
            "sparkWorkerSha256": worker_alias["sha256"],
            "implementationSetSha256": implementation_digest,
            "r2FitFailureSha256": ev.sha256_file(r2_failure),
            "recoveryReferenceSha256": ev.sha256_file(
                self.fit_dir / "recovery-reference.json"),
            "executionProfileSha256": ev.sha256_file(
                self.fit_dir / "execution-profile.json"),
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
            "recovery_reference": self.fit_dir / "recovery-reference.json",
            "execution_profile": self.fit_dir / "execution-profile.json",
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
        self._spark_review(
            self.fit_review, "fit", fit_target_paths, fit_lock, output_parent,
            [*dependency_files, self.preflight_review])

        fit_controls = [root_pin(path, self.root) for path in sorted(
            (path for path in self.fit_dir.rglob("*") if path.is_file()),
            key=lambda path: path.as_posix())]
        fit_controls.append(root_pin(self.fit_review, self.root))
        fit_controls.extend(preflight_controls)

        self.score_dir = output_parent / (ev.RUN_ID + "-score")
        (self.score_dir / "score").mkdir(parents=True)
        self.predictions = self.score_dir / "score/predictions.parquet"
        b1 = ratings + np.tile(np.array([.02, -.01, .01, -.02]), 7)
        pq.write_table(pa.table({
            "row_id": pa.array(np.arange(28), type=pa.int64()),
            "uid": pa.array(score_uids, type=pa.int32()),
            "prediction": pa.array(b1, type=pa.float64()),
        }), self.predictions)
        score_input_sources = [source_record("source/natural-score.parquet")]
        score_all_records = [*model_sources, *score_input_sources, *runtime_sources,
                             *execution_sources, *fit_controls]
        score_lock = {
            "schemaVersion": "feelm-service-v1-b1-score-input-lock/2",
            "phase": "score", "runId": ev.RUN_ID, "profileId": ev.PROFILE_ID,
            "modelInputRecords": model_sources, "scoreInputRecords": score_input_sources,
            "workerRuntimeRecords": runtime_sources, "executionRecords": execution_sources,
            "controlReferences": fit_controls,
            "modelInputSetSha256": ev.canonical_record_set_digest(model_sources),
            "scoreInputSetSha256": ev.canonical_record_set_digest(score_input_sources),
            "workerRuntimeSetSha256": ev.canonical_record_set_digest(runtime_sources),
            "executionSetSha256": ev.canonical_record_set_digest(execution_sources),
            "controlReferenceSetSha256": ev.canonical_record_set_digest(fit_controls),
            "inputSetSha256": ev.canonical_record_set_digest(score_all_records),
            "r2TrainingSourceSetSha256": r2_training_digest,
            "evaluationTargetsRead": False,
        }
        dump_json(self.score_dir / "fit-reference.json", {
            "schemaVersion": "feelm-service-v1-b1-fit-reference/2",
            "runId": ev.RUN_ID, "profileId": ev.PROFILE_ID,
            "fitManifest": root_pin(self.fit_manifest, self.root),
            "fitReview": root_pin(self.fit_review, self.root),
            "modelInventory": root_pin(model_inventory, self.root),
            "modelFileSetSha256": model_digest,
            "modelFiles": model_records,
            "reviewedArtifacts": fit_controls, "recoveryReference": embedded_recovery,
            "outerRunner": outer_alias, "sparkWorker": worker_alias,
            "executionProfile": profile_record,
            "modelInputSetSha256": score_lock["modelInputSetSha256"],
            "workerRuntimeSetSha256": score_lock["workerRuntimeSetSha256"],
            "executionSetSha256": score_lock["executionSetSha256"],
            "scoreInputSetSha256": score_lock["scoreInputSetSha256"],
            "scorePhaseInputSetSha256": score_lock["inputSetSha256"],
        })
        dump_json(self.score_dir / "score-input-lock.json", score_lock)
        dump_json(self.score_dir / "recovery-reference.json", recovery)
        self._phase_profile(self.score_dir / "execution-profile.json", "score",
                            profile_record, outer_alias, score_lock["executionSetSha256"])
        dump_json(self.score_dir / "command.json", {"action": "score"})
        self._text_at(self.score_dir / "score/analyzed-plan.txt", "Project row_id,uid,prediction\n")
        dump_json(self.score_dir / "resource.json", {"status": "PASS"})
        self._text_at(self.score_dir / "run.log", "score complete\n")
        required = {
            "fit-reference.json", "score-input-lock.json", "command.json",
            "score/analyzed-plan.txt", "score/predictions.parquet", "resource.json", "run.log",
            "execution-profile.json", "recovery-reference.json",
        }
        self.score_manifest = self.score_dir / "manifest.json"
        dump_json(self.score_manifest, {
            "schemaVersion": "feelm-service-v1-b1-score-manifest/2",
            "runId": ev.RUN_ID, "profileId": ev.PROFILE_ID,
            "status": "B1_NATURAL_SCORE_COMPLETE_AUDIT_PENDING",
            "evaluationAuthorized": False, "readyForService": False,
            "modelFitPerformed": False, "scorePerformed": True,
            "inputSetSha256": score_lock["inputSetSha256"],
            "modelInputSetSha256": score_lock["modelInputSetSha256"],
            "scoreInputSetSha256": score_lock["scoreInputSetSha256"],
            "workerRuntimeSetSha256": score_lock["workerRuntimeSetSha256"],
            "executionSetSha256": score_lock["executionSetSha256"],
            "controlReferenceSetSha256": score_lock["controlReferenceSetSha256"],
            "fitManifestSha256": ev.sha256_file(self.fit_manifest),
            "fitReviewSha256": ev.sha256_file(self.fit_review),
            "modelInventorySha256": ev.sha256_file(model_inventory),
            "modelFileSetSha256": model_digest,
            "outerRunnerSha256": outer_alias["sha256"],
            "sparkWorkerSha256": worker_alias["sha256"],
            "implementationSetSha256": implementation_digest,
            "r2FitFailureSha256": ev.sha256_file(r2_failure),
            "recoveryReferenceSha256": ev.sha256_file(
                self.score_dir / "recovery-reference.json"),
            "executionProfileSha256": ev.sha256_file(
                self.score_dir / "execution-profile.json"),
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
            "execution_profile": root_pin(self.score_dir / "execution-profile.json", self.root),
            "recovery_reference": root_pin(self.score_dir / "recovery-reference.json", self.root),
            "outer_runner": root_pin(self.outer, self.root), "spark_worker": root_pin(self.worker, self.root),
        }
        self._spark_review(
            self.score_review, "score", {name: (
                self.score_manifest if name == "manifest" else
                self.score_dir / {"fit_reference": "fit-reference.json",
                                  "score_input_lock": "score-input-lock.json",
                                  "command": "command.json",
                                  "analyzed_plan": "score/analyzed-plan.txt",
                                  "predictions": "score/predictions.parquet",
                                  "resource": "resource.json", "run_log": "run.log",
                                  "execution_profile": "execution-profile.json",
                                  "recovery_reference": "recovery-reference.json"}[name]
                if name not in {"outer_runner", "spark_worker"} else
                (self.outer if name == "outer_runner" else self.worker))
             for name in target}, score_lock, output_parent,
            [*dependency_files, self.preflight_review, self.fit_review, self.score_axis])

        self.paths = ev.EvaluationPaths(
            root=self.root, team_root=self.team_root, plan=self.plan,
            artifact_manifest=self.artifact_manifest,
            evaluation_contract=self.contract, contexts=self.contexts, catalog=self.catalog,
            labels=self.labels, evaluation_seal=self.eval_seal, roles=self.roles,
            metadata=self.metadata, score_axis=self.score_axis, ratings=self.ratings,
            b0_predictions=self.b0, b0_seal=self.b0_seal,
            als_factor_dir=self.als_dir, score_manifest=self.score_manifest,
            score_review=self.score_review, spark_auditor=self.spark_auditor,
            evaluation_auditor=EVALUATION_AUDITOR_PATH,
        )
        self.fixed_pins = {name: (ev.pin(getattr(self.paths, name))["bytes"],
                                  ev.pin(getattr(self.paths, name))["sha256"])
                           for name in ev.SOURCE_PINS}

    def _text(self, relative: str, content: str) -> Path:
        if relative.startswith("team/"):
            path = self.team_root / relative.removeprefix("team/")
        else:
            path = self.root / relative
        self._text_at(path, content)
        return path

    def _logical(self, path: Path) -> str:
        resolved = path.resolve()
        if resolved.is_relative_to(self.root.resolve()):
            return "standalone/" + resolved.relative_to(self.root.resolve()).as_posix()
        if resolved.is_relative_to(self.team_root.resolve()):
            return "team/" + resolved.relative_to(self.team_root.resolve()).as_posix()
        raise ValueError(path)

    def _phase_profile(self, path: Path, phase: str, profile_record: dict,
                       outer_record: dict, execution_digest: str) -> None:
        dump_json(path, {
            "schemaVersion": "feelm-service-v1-b1-phase-execution-profile/1",
            "runId": ev.RUN_ID, "profileId": ev.PROFILE_ID, "phase": phase,
            "profileContract": profile_record, "outerRunner": outer_record,
            "executionSetSha256": execution_digest,
            "resolved": json.loads(self.profile.read_text(encoding="utf-8")),
        })

    def _group_lock(self, phase: str, models: list[dict], runtime: list[dict],
                    execution: list[dict], controls: list[dict],
                    r2_digest: str) -> dict:
        return {
            "schemaVersion": ("feelm-service-v1-b1-score-input-lock/2"
                              if phase == "score" else "feelm-service-v1-b1-input-lock/2"),
            "phase": phase, "runId": ev.RUN_ID, "profileId": ev.PROFILE_ID,
            "modelInputRecords": models, "workerRuntimeRecords": runtime,
            "executionRecords": execution, "controlReferences": controls,
            "modelInputSetSha256": ev.canonical_record_set_digest(models),
            "workerRuntimeSetSha256": ev.canonical_record_set_digest(runtime),
            "executionSetSha256": ev.canonical_record_set_digest(execution),
            "controlReferenceSetSha256": ev.canonical_record_set_digest(controls),
            "inputSetSha256": ev.canonical_record_set_digest(
                [*models, *runtime, *execution, *controls]),
            "r2TrainingSourceSetSha256": r2_digest,
            "evaluationTargetsRead": False,
        }

    @staticmethod
    def _bundle_inventory(bundle: Path) -> dict:
        return {
            path.relative_to(bundle).as_posix(): ev.pin(path)
            for path in sorted(bundle.rglob("*")) if path.is_file()
        }

    def _spark_review(self, path: Path, phase: str, targets: dict[str, Path],
                      lock: dict, output_parent: Path,
                      dependency_files: list[Path]) -> None:
        order = ["preflight"] if phase == "preflight" else (
            ["preflight", "fit"] if phase == "fit" else ["preflight", "fit", "score"])
        bundles = [output_parent / f"{ev.R2_RUN_ID}-preflight"] + [
            output_parent / f"{ev.RUN_ID}-{name}" for name in order]
        target = {name: root_pin(value, self.root) for name, value in targets.items()}
        lock_key = "score_input_lock" if phase == "score" else "input_lock"
        target[lock_key].update({
            key: lock[key] for key in (
                "modelInputSetSha256", "workerRuntimeSetSha256", "executionSetSha256",
                "controlReferenceSetSha256", "inputSetSha256")
        })
        if phase == "score":
            target[lock_key]["scoreInputSetSha256"] = lock["scoreInputSetSha256"]
        resource = {
            "stages": 2 if phase == "preflight" else 1,
            "peakBytes": 1, "cleanupConfirmed": True, "labelMountAbsent": True,
            "measurementIndependentlyObserved": False,
            "measurementCheckedAgainstPinnedWorkerLog": True,
        }
        recovery = {
            "r2FitFailure": root_pin(self.r2_failure, self.root),
            "reference": ev.pin(targets["recovery_reference"]),
        }
        identity = {
            "sourceRows": self.spec.training_rows,
            "logicalRows": 4 * self.spec.training_rows,
            "partitions": 8,
            "allSourceRowsRead": True,
            "maskedFormulaRecomputed": False,
            "maskedArtifactPinsChecked": True,
        }
        checks = {
            "preflight": {
                "resource": resource, "recovery": recovery,
                "identity": identity,
            },
            "fit": {
                "resource": resource, "recovery": recovery,
                "parentChain": "PASS", "identity": identity,
                "fit": {
                    "treeCount": 1,
                    "thresholdParity": {
                        "rows": 3, "nonLeafSplitCount": 1,
                        "maxAbsError": 0.0, "status": "PASS",
                    },
                    "weightedTrainRmseIndependentlyRecomputed": False,
                    "trainingQualityNotAnAcceptanceMetric": True,
                },
            },
            "score": {
                "resource": resource, "recovery": recovery,
                "parentChain": "PASS", "predictions": {
                    "rows": self.spec.score_rows,
                    "uniqueRowIds": self.spec.score_rows,
                    "strictlyIncreasing": True, "uidParity": True,
                    "finite": True, "maxAbsError": 0.0,
                    "labelRead": False, "singlePhysicalFile": True,
                },
            },
        }[phase]
        dump_json(path, {
            "schemaVersion": "feelm-service-v1-b1-r3-result-review/1",
            "runId": ev.RUN_ID, "profileId": ev.PROFILE_ID,
            "status": "PASS", "phase": phase, "createdAt": "2026-09-14T00:00:00Z",
            "target": target,
            "reviewer": {"implementation": ev.pin(self.spark_auditor),
                         "independentFromRunner": True},
            "dependencyFingerprint": {
                "files": {
                    ("ancestor/service-v1-b1-spark-runner.md"
                     if item.resolve() == self.r2_plan.resolve()
                     else self._logical(item)): ev.pin(item)
                    for item in sorted(set(dependency_files))
                },
                "bundleInventories": {
                    self._logical(bundle): self._bundle_inventory(bundle) for bundle in bundles
                },
                "auditorImplementation": ev.pin(self.spark_auditor),
                "dockerImageId": ev.IMAGE_ID, "runId": ev.RUN_ID,
                "profileId": ev.PROFILE_ID,
            },
            "checks": checks, "evaluationTargetsRead": False,
            "modelFitPerformed": False, "readyForService": False,
            "scope": ("Local immutable bundle integrity and specified numerical parity; "
                      "no service acceptance or model quality verdict."),
        })

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
        selection_lock = json.loads((output / "evaluation-input-lock.json").read_text(
            encoding="utf-8"))
        selection_truth = json.loads((output / "truth-integrity.json").read_text(
            encoding="utf-8"))
        command = json.loads((output / "command.json").read_text(encoding="utf-8"))
        dump_json(review_path, {
            "schemaVersion": "feelm-service-v1-b1-r3-evaluation-result-review/1",
            "runId": ev.RUN_ID, "profileId": ev.PROFILE_ID,
            "status": "PASS", "phase": "selection", "createdAt": "2026-09-14T00:00:00Z",
            "target": target,
            "reviewer": {
                "implementation": root_pin(self.paths.evaluation_auditor, self.root),
                "independentFromEvaluator": True,
            },
            "dependencyFingerprint": ev._selection_review_fingerprint(
                output, selection_lock, self.root, self.team_root,
                self.paths.evaluation_auditor),
            "checks": {
                "exactOutputInventory": True, "allInputPinsRehashed": True,
                "r2AncestorChainVerified": True, "truthRebuilt": True,
                "affineIndependentlyRecomputed": True,
                "metricsIndependentlyRecomputed": True,
                "bootstrapSamples": command["bootstrapSamples"],
                "gateIndependentlyRecomputed": True,
                "truthRows": selection_truth["joinedRows"],
            },
            "decision": {
                "selectionGate": "PASS", "confirmationEligible": True,
                "logicalState": "B1_SELECTION_AUDITED_CONFIRMATION_ELIGIBLE",
            },
            "evaluationInputsRead": True, "modelFitPerformed": False,
            "readyForService": False,
            "scope": ("Local immutable evaluation integrity and independent metric/gate parity; "
                      "no service activation."),
        })
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
    def _run_confirmation(self, fixture: SyntheticFixture, selection: Path,
                          review: Path, output: Path, *, publish_failure: bool = False) -> dict:
        return ev.run_confirmation(
            fixture.paths, output,
            expected_plan_sha256=ev.sha256_file(fixture.plan),
            expected_score_manifest_sha256=ev.sha256_file(fixture.score_manifest),
            expected_score_review_sha256=ev.sha256_file(fixture.score_review),
            selection_manifest_path=selection / "manifest.json",
            selection_review_path=review,
            expected_selection_manifest_sha256=ev.sha256_file(selection / "manifest.json"),
            expected_selection_review_sha256=ev.sha256_file(review),
            spec=fixture.spec, fixed_pins=fixture.fixed_pins,
            publish_failure=publish_failure)

    def _replace_with_same_byte_symlink(self, source: Path, target: Path) -> bool:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source.unlink()
        try:
            os.symlink(target, source, target_is_directory=False)
        except OSError:
            # Locked-down Windows hosts cannot create file symlinks. Restore the
            # bytes and let the caller inject the same lstat classification so
            # the fail-closed branch remains covered in normal and -O runs.
            shutil.copy2(target, source)
            return False
        return True

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
            self.assertIn("standalone/inputs/ratings.parquet", source_names)
            self.assertIn("standalone/scripts/run_service_v1_b1_gbt_r3.py", source_names)
            self.assertIn("standalone/scripts/service_v1_b1_spark_worker.py", source_names)
            evidence_root = (
                "standalone/outputs/recommendation-evidence/"
                "service-v1-pretraining-20260913/")
            self.assertIn(evidence_root + ev.R2_RUN_ID + "-fit-failure.json",
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
            with self.assertRaisesRegex(ValueError, "selection independent review identity/status drift"):
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
            score_lock = ev.read_json(fixture.score_dir / "score-input-lock.json")
            with self.assertRaisesRegex(ValueError, "fit independent review field set drift"):
                ev.verify_fit_reference(fixture.paths, fit_reference_path, reference,
                                        score_manifest, score_target, score_lock,
                                        score_lock["controlReferences"])

    def test_ancestor_file_mutation_is_rehashed_before_labels_are_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            with (fixture.preflight_dir / "command.json").open("a", encoding="utf-8") as handle:
                handle.write(" ")
            fixture.labels.unlink()
            with self.assertRaisesRegex(ValueError, "command.json byte drift"):
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
            ("runId", "b1-gbt120-s339-v1-r3", "r3 score manifest identity/status drift"),
            ("schemaVersion", "feelm-service-v1-b1-score-manifest/1",
             "r3 score manifest identity/status drift"),
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
            with self.assertRaisesRegex(ValueError, "score input lock full input digest drift"):
                ev.verify_r3_score_lock(lock, label="score input lock",
                                        canonical_contract=False)

            preflight_reference_path = fixture.fit_dir / "preflight-reference.json"
            preflight_reference = ev.read_json(preflight_reference_path)
            preflight_reference["fitInputSetSha256"] = "0" * 64
            fit_reference = ev.read_json(fixture.score_dir / "fit-reference.json")
            fit_manifest = ev.read_json(fixture.fit_manifest)
            fit_lock = ev.read_json(fixture.fit_dir / "input-lock.json")
            fit_models, fit_runtime, fit_execution, fit_controls = ev.verify_r3_grouped_lock(
                fit_lock, phase="fit", label="fit input lock", canonical_contract=False)
            with self.assertRaisesRegex(ValueError, "preflight reference fit lock digest drift"):
                ev._verify_preflight_ancestry(
                    fixture.paths,
                    preflight_reference_path=preflight_reference_path,
                    preflight_reference=preflight_reference,
                    fit_manifest=fit_manifest,
                    fit_groups=(fit_models, fit_runtime, fit_execution),
                    fit_controls=fit_controls,
                    fit_lock=fit_lock,
                    outer_path=fixture.outer,
                    worker_path=fixture.worker,
                    outer_record=fit_reference["outerRunner"],
                    worker_record=fit_reference["sparkWorker"],
                    profile_record=fit_reference["executionProfile"],
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

    def test_r3_logical_namespace_rejects_alias_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            lock_path = fixture.score_dir / "score-input-lock.json"
            lock = ev.read_json(lock_path)
            forged_execution = [dict(record) for record in lock["executionRecords"]]
            outer = next(record for record in forged_execution
                         if record["path"] == "execution/run_service_v1_b1_gbt_r3.py")
            outer["path"] = "implementation/run_service_v1_b1_gbt_r3.py"
            with self.assertRaisesRegex(ValueError, "path set drift"):
                ev.verify_r3_score_groups(
                    lock["modelInputRecords"], lock["scoreInputRecords"],
                    lock["workerRuntimeRecords"], forged_execution,
                    paths=fixture.paths, owner=lock_path, label="forged score")

            escaped = fixture._text("escape-target.json", "{}\n")
            owner = fixture._text("nested/reference.json", "{}\n")
            record = {"path": "nested/../escape-target.json", **ev.pin(escaped)}
            with self.assertRaisesRegex(ValueError, "traversal forbidden"):
                ev.resolve_record_path(
                    record, root=fixture.root, team_root=fixture.team_root,
                    owner=owner)

    def test_score_input_group_is_exact_disjoint_and_single_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            lock = ev.read_json(fixture.score_dir / "score-input-lock.json")
            self.assertEqual([record["path"] for record in lock["scoreInputRecords"]],
                             ["source/natural-score.parquet"])
            forged = dict(lock)
            forged["scoreInputRecords"] = [dict(lock["modelInputRecords"][0])]
            forged["scoreInputSetSha256"] = ev.canonical_record_set_digest(
                forged["scoreInputRecords"])
            with self.assertRaisesRegex(ValueError, "record groups overlap"):
                ev.verify_r3_score_lock(
                    forged, label="forged score lock", canonical_contract=False)

    def test_forged_spark_review_check_shape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            review = ev.read_json(fixture.score_review)
            review["checks"] = {"predictions": {"status": "PASS"}}
            with self.assertRaisesRegex(ValueError,
                                        "score independent review check set drift"):
                ev._require_review_envelope(
                    review, "score", paths=fixture.paths,
                    review_path=fixture.score_review)

    def test_spark_review_nested_evidence_is_exact_for_every_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            cases = (
                ("preflight", fixture.preflight_review, "identity"),
                ("fit", fixture.fit_review, "fit"),
                ("score", fixture.score_review, "predictions"),
            )
            for phase, review_path, nested_key in cases:
                with self.subTest(phase=phase, nested_key=nested_key):
                    review = ev.read_json(review_path)
                    review["checks"][nested_key] = {}
                    with self.assertRaisesRegex(ValueError, "evidence drift"):
                        ev._require_review_envelope(
                            review, phase, paths=fixture.paths,
                            review_path=review_path)

    def test_spark_review_target_pin_types_case_and_fields_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            original = ev.read_json(fixture.score_review)
            target_path = fixture.score_manifest
            cases = (
                ("bytes", lambda r: r.__setitem__("bytes", str(r["bytes"])),
                 "invalid pin byte"),
                ("sha-case", lambda r: r.__setitem__("sha256", r["sha256"].upper()),
                 "invalid lowercase pin"),
                ("extra", lambda r: r.__setitem__("extra", True), "field set drift"),
            )
            for name, mutate, message in cases:
                with self.subTest(name=name):
                    review = json.loads(json.dumps(original))
                    mutate(review["target"]["manifest"])
                    with self.assertRaisesRegex(ValueError, message):
                        ev._target_pin(
                            review, "manifest", target_path, root=fixture.root,
                            team_root=fixture.team_root,
                            review_path=fixture.score_review, phase="score")

    def test_grouped_lock_records_reject_unhashed_extra_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            lock_path = fixture.score_dir / "score-input-lock.json"
            lock = ev.read_json(lock_path)
            model_records = [dict(record) for record in lock["modelInputRecords"]]
            model_records[0]["ignored"] = "forged"
            with self.assertRaisesRegex(ValueError, "record fields drift"):
                ev.verify_r3_score_groups(
                    model_records, lock["scoreInputRecords"],
                    lock["workerRuntimeRecords"], lock["executionRecords"],
                    paths=fixture.paths, owner=lock_path, label="forged score")

            controls = [dict(record) for record in lock["controlReferences"]]
            controls[0]["ignored"] = "forged"
            expected = [ev.resolve_record_path(
                record, root=fixture.root, team_root=fixture.team_root,
                owner=lock_path) for record in lock["controlReferences"]]
            with self.assertRaisesRegex(ValueError, "record fields drift"):
                ev._verify_control_records(
                    controls, expected, paths=fixture.paths, owner=lock_path,
                    label="forged controls")

    def test_failure_is_self_contained_and_existing_failure_is_never_clobbered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            fixture.score_review.unlink()
            output = fixture.root / "outputs/selection-failure-case"
            with self.assertRaises(ValueError):
                ev.run_selection(
                    fixture.paths, output,
                    expected_plan_sha256=ev.sha256_file(fixture.plan),
                    expected_score_manifest_sha256=ev.sha256_file(
                        fixture.score_manifest),
                    expected_score_review_sha256="0" * 64,
                    spec=fixture.spec, fixed_pins=fixture.fixed_pins,
                    publish_failure=True)
            failure = output.with_name(output.name + "-failure.json")
            payload = ev.read_json(failure)
            self.assertEqual(payload["schemaVersion"],
                             "feelm-service-v1-b1-evaluation-failure/1")
            self.assertEqual(payload["runId"], ev.RUN_ID)
            self.assertEqual(payload["profileId"], ev.PROFILE_ID)
            self.assertEqual(payload["phase"], "calibrate-select")
            self.assertEqual(payload["status"], "FAILED")
            self.assertTrue(payload["cleanupComplete"])
            self.assertEqual(payload["evidence"]["expectedScoreReviewSha256"],
                             "0" * 64)
            self.assertEqual(payload["evidence"]["scoreManifestPath"],
                             str(fixture.score_manifest))
            self.assertFalse(output.exists())
            self.assertFalse(list(output.parent.glob("." + output.name + ".tmp-*")))

            preserved = failure.read_bytes()
            with self.assertRaisesRegex(ValueError, "preserve existing immutable failure"):
                ev.run_selection(
                    fixture.paths, output,
                    expected_plan_sha256=ev.sha256_file(fixture.plan),
                    expected_score_manifest_sha256=ev.sha256_file(
                        fixture.score_manifest),
                    expected_score_review_sha256="0" * 64,
                    spec=fixture.spec, fixed_pins=fixture.fixed_pins,
                    publish_failure=True)
            self.assertEqual(failure.read_bytes(), preserved)

    def test_selection_review_full_envelope_and_current_reviewer_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            selection = fixture.root / "outputs/selection"
            fixture.run_selection(selection)
            review = fixture.review_selection(selection)
            manifest_sha = ev.sha256_file(selection / "manifest.json")
            original = ev.read_json(review)
            # The valid full review is accepted before trying forged variants.
            ev.verify_selection_gate(
                selection / "manifest.json", review, manifest_sha,
                ev.sha256_file(review), fixture.root, fixture.team_root,
                fixture.paths.evaluation_auditor)
            cases = (
                ("extra", lambda p: p.__setitem__("forged", True), "field set drift"),
                ("reviewer", lambda p: p["reviewer"].__setitem__(
                    "independentFromEvaluator", False), "reviewer identity/pin drift"),
                ("fingerprint", lambda p: p["dependencyFingerprint"].__setitem__(
                    "runId", "forged"), "dependency fingerprint drift"),
                ("checks", lambda p: p["checks"].__setitem__(
                    "metricsIndependentlyRecomputed", False), "checks drift"),
                ("decision", lambda p: p["decision"].__setitem__(
                    "confirmationEligible", False), "decision drift"),
                ("safety", lambda p: p.__setitem__("readyForService", True),
                 "safety/scope drift"),
            )
            for name, mutate, message in cases:
                with self.subTest(name=name):
                    payload = json.loads(json.dumps(original))
                    mutate(payload)
                    review.unlink()
                    dump_json(review, payload)
                    # Supplying the forged file's correct self-SHA is insufficient.
                    with self.assertRaisesRegex(ValueError, message):
                        ev.verify_selection_gate(
                            selection / "manifest.json", review, manifest_sha,
                            ev.sha256_file(review), fixture.root, fixture.team_root,
                            fixture.paths.evaluation_auditor)
            review.unlink()
            dump_json(review, original)

    def test_invalid_selection_decision_is_rejected_before_source_fingerprint_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            selection = fixture.root / "outputs/selection"
            fixture.run_selection(selection)
            review = fixture.review_selection(selection)
            payload = ev.read_json(review)
            payload["decision"]["confirmationEligible"] = False
            review.unlink()
            dump_json(review, payload)
            with mock.patch.object(
                    ev, "_selection_review_fingerprint",
                    side_effect=AssertionError("source fingerprint must stay closed")) as replay:
                with self.assertRaisesRegex(ValueError, "decision drift"):
                    ev.verify_selection_gate(
                        selection / "manifest.json", review,
                        ev.sha256_file(selection / "manifest.json"),
                        ev.sha256_file(review), fixture.root, fixture.team_root,
                        fixture.paths.evaluation_auditor)
            replay.assert_not_called()

    def test_selection_same_basename_outside_public_namespace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            original = fixture.root / "outputs/selection"
            fixture.run_selection(original)
            original_review = fixture.review_selection(original)
            crafted = fixture.root / "crafted" / ev.EVALUATION_SELECTION_NAME
            shutil.copytree(original, crafted)
            crafted_review = crafted.with_name(crafted.name + "-result-review.json")
            shutil.copy2(original_review, crafted_review)
            with mock.patch.object(ev, "ROOT", fixture.root):
                with self.assertRaisesRegex(ValueError, "noncanonical lexical path"):
                    ev.verify_selection_gate(
                        crafted / "manifest.json", crafted_review,
                        ev.sha256_file(crafted / "manifest.json"),
                        ev.sha256_file(crafted_review), fixture.root, fixture.team_root,
                        fixture.paths.evaluation_auditor)

    def test_fit_and_preflight_same_basename_wrong_parent_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            for phase in ("preflight", "fit"):
                with self.subTest(phase=phase):
                    replay = fixture.root / "replay" / f"{ev.RUN_ID}-{phase}"
                    replay.mkdir(parents=True)
                    with mock.patch.object(ev, "ROOT", fixture.root):
                        with self.assertRaisesRegex(
                                ValueError, f"r3 {phase} bundle noncanonical lexical path"):
                            ev._require_canonical_spark_phase_bundle(
                                fixture.paths, replay, phase)

    def test_public_preflight_review_uses_the_canonical_two_root_namespace(self) -> None:
        review_path = (
            ev.ROOT / "outputs/recommendation-evidence/service-v1-pretraining-20260913" /
            f"{ev.RUN_ID}-preflight-result-review.json")
        self.assertTrue(review_path.is_file(), "public r3 preflight review fixture is required")
        review = ev.read_json(review_path)
        ev._validate_spark_review_checks(review["checks"], "preflight", canonical=True)
        logical_paths = [record["path"] for record in review["target"].values()]
        logical_paths.extend(review["dependencyFingerprint"]["files"])
        self.assertIn("ancestor/service-v1-b1-spark-runner.md", logical_paths)
        self.assertTrue(all(path.startswith(("standalone/", "team/", "ancestor/"))
                            for path in logical_paths))
        self.assertFalse(any(path.startswith("../") for path in logical_paths))

    def test_confirmation_gate_rejects_selection_failure_and_stale_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            selection = fixture.root / "outputs/selection"
            fixture.run_selection(selection)
            review = fixture.review_selection(selection)
            arguments = (
                selection / "manifest.json", review,
                ev.sha256_file(selection / "manifest.json"), ev.sha256_file(review),
                fixture.root, fixture.team_root, fixture.paths.evaluation_auditor,
            )
            failure = selection.with_name(selection.name + "-failure.json")
            failure.write_text("{}\n", encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "coexists with failure evidence"):
                ev.verify_selection_gate(*arguments)
            failure.unlink()
            stale_names = (
                "." + selection.name + ".tmp-race",
                "." + review.name + ".tmp-race",
                "." + failure.name + ".tmp-race",
                "." + selection.name + ".worker-scratch-race",
                "." + selection.name + ".publication-claim",
            )
            for name in stale_names:
                with self.subTest(name=name):
                    stale = selection.parent / name
                    stale.write_text("stale\n", encoding="utf-8", newline="\n")
                    with self.assertRaisesRegex(ValueError, "stale temp/scratch"):
                        ev.verify_selection_gate(*arguments)
                    stale.unlink()

    def test_evaluation_auditor_core_pin_normalizes_only_evaluator_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = EVALUATION_AUDITOR_PATH.read_text(encoding="utf-8")
            first = root / "audit.py"
            first.write_text(original, encoding="utf-8", newline="\n")
            baseline = ev.normalized_evaluation_auditor_sha256(first)
            _, current_embedded = ev.evaluation_auditor_contract(first)
            changed_pin = original.replace(
                '"' + current_embedded + '"', '"' + "1" * 64 + '"', 1)
            self.assertNotEqual(changed_pin, original)
            second = root / "audit-pin-only.py"
            second.write_text(changed_pin, encoding="utf-8", newline="\n")
            self.assertEqual(baseline, ev.normalized_evaluation_auditor_sha256(second))
            with self.assertRaisesRegex(ValueError, "embeds a stale evaluator"):
                ev.verify_evaluation_auditor_binding(second, ev.SCRIPT, baseline)
            behavioral = root / "audit-behavioral.py"
            behavioral.write_text(changed_pin + "\nBEHAVIORAL_DRIFT = True\n",
                                  encoding="utf-8", newline="\n")
            self.assertNotEqual(baseline,
                                ev.normalized_evaluation_auditor_sha256(behavioral))
            duplicate = root / "audit-duplicate.py"
            duplicate.write_text(changed_pin +
                                 '\nREVIEWED_R3_EVALUATOR_SHA256 = "' + "2" * 64 + '"\n',
                                 encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "binding must be unique"):
                ev.normalized_evaluation_auditor_sha256(duplicate)

            nested = root / "audit-nested.py"
            nested.write_text(changed_pin + (
                '\ndef overwrite():\n'
                '    REVIEWED_R3_EVALUATOR_SHA256 = "' + "3" * 64 + '"\n'),
                encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "binding must be unique"):
                ev.normalized_evaluation_auditor_sha256(nested)

    def test_production_evaluator_has_no_assert_or_public_bypass(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))
        for forbidden in ("--bootstrap-samples", "--force", "--overwrite",
                          "--skip-review", "--allow-noncanonical"):
            self.assertNotIn(forbidden, source)

    def test_public_seal_rejects_nonhex_and_zero_placeholders(self) -> None:
        for name, value in (("nonhex", "z" * 64), ("zero", "0" * 64)):
            with self.subTest(name=name), mock.patch.object(
                    ev, "REVIEWED_R3_EVALUATION_AUDITOR_CORE_SHA256", value):
                with self.assertRaisesRegex(ValueError, "PUBLIC EVALUATION BLOCKED"):
                    ev.require_public_r3_contract_sealed()

    def test_linked_score_and_native_members_are_rejected_even_with_same_bytes(self) -> None:
        for name, member in (
            ("score", lambda f: f.predictions),
            ("native", lambda f: f.fit_dir / "model/native/data/part-00000"),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                fixture = SyntheticFixture(Path(temporary))
                source = member(fixture)
                target = fixture.root / "linked-targets" / (name + ".bin")
                real_link = self._replace_with_same_byte_symlink(source, target)
                original_detector = ev._is_link_or_reparse

                def detector(path: Path) -> bool:
                    return (Path(path) == source if not real_link else False) or original_detector(path)

                with mock.patch.object(ev, "_is_link_or_reparse", side_effect=detector):
                    with self.assertRaisesRegex(ValueError, "linked/reparse"):
                        fixture.run_selection(fixture.root / "outputs/selection")

    def test_broken_output_link_is_existing_and_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticFixture(Path(temporary))
            output = fixture.root / "outputs/selection-link"
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.symlink(fixture.root / "missing-target", output, target_is_directory=True)
            except OSError:
                output.write_text("namespace entry\n", encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "preserve existing.*output"):
                fixture.run_selection(output)
            self.assertTrue(os.path.lexists(output))
            self.assertFalse(os.path.lexists(output.with_name(output.name + "-failure.json")))

    def test_selection_and_confirmation_publish_races_never_create_failure_siblings(self) -> None:
        phases = ("selection", "confirmation")
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                fixture = SyntheticFixture(Path(temporary))
                selection = fixture.root / "outputs/selection"
                review: Path | None = None
                if phase == "confirmation":
                    fixture.run_selection(selection)
                    review = fixture.review_selection(selection)
                output = fixture.root / "outputs" / (phase + "-race")

                def peer_wins(_source: Path, destination: Path) -> None:
                    destination.mkdir()
                    (destination / "peer-success.txt").write_text(
                        "peer won\n", encoding="utf-8", newline="\n")
                    raise FileExistsError("peer won")

                with mock.patch.object(ev, "_rename_no_replace", side_effect=peer_wins):
                    with self.assertRaisesRegex(ValueError,
                                                "preserve existing immutable output"):
                        if phase == "selection":
                            ev.run_selection(
                                fixture.paths, output,
                                expected_plan_sha256=ev.sha256_file(fixture.plan),
                                expected_score_manifest_sha256=ev.sha256_file(
                                    fixture.score_manifest),
                                expected_score_review_sha256=ev.sha256_file(
                                    fixture.score_review),
                                spec=fixture.spec, fixed_pins=fixture.fixed_pins,
                                publish_failure=True)
                        else:
                            if review is None:
                                raise AssertionError("confirmation review fixture missing")
                            self._run_confirmation(
                                fixture, selection, review, output,
                                publish_failure=True)
                self.assertEqual((output / "peer-success.txt").read_text(encoding="utf-8"),
                                 "peer won\n")
                self.assertFalse(os.path.lexists(
                    output.with_name(output.name + "-failure.json")))

    def test_success_and_failure_publishers_share_one_atomic_namespace_claim(self) -> None:
        for preferred in ("success", "failure"):
            with self.subTest(preferred=preferred), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "result"
                barrier = threading.Barrier(2)
                outcomes: dict[str, tuple[Path, ev._PublicationLease] | BaseException] = {}

                def contender(name: str) -> None:
                    barrier.wait()
                    try:
                        outcomes[name] = ev._new_staging(output)
                    except BaseException as error:
                        outcomes[name] = error

                threads = [threading.Thread(target=contender, args=(name,))
                           for name in ("one", "two")]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive(), "publication race deadlocked")

                winners = [value for value in outcomes.values() if isinstance(value, tuple)]
                losers = [value for value in outcomes.values() if isinstance(value, BaseException)]
                self.assertEqual(len(winners), 1)
                self.assertEqual(len(losers), 1)
                staging, lease = winners[0]
                if preferred == "success":
                    (staging / "payload.txt").write_text(
                        "success\n", encoding="utf-8", newline="\n")
                    ev._publish(staging, output, lease)
                    self.assertEqual(ev._bundle_inventory(output), {"payload.txt"})
                else:
                    shutil.rmtree(staging)
                    ev._atomic_failure(
                        output, "race", RuntimeError("boom"), True, lease=lease)
                    self.assertEqual(ev.read_json(ev._failure_path(output))["status"], "FAILED")

                failure_path = ev._failure_path(output)
                self.assertNotEqual(os.path.lexists(output), os.path.lexists(failure_path))
                self.assertFalse(os.path.lexists(ev._namespace_claim_path(output)))
                self.assertFalse(list(root.glob(".result.tmp-*")))
                self.assertFalse(list(root.glob(".result-failure.json.tmp-*")))

    def test_stale_peer_transients_block_staging_and_failure_publication(self) -> None:
        names = (
            ".result.tmp-old",
            ".result-result-review.json.tmp-old",
            ".result-failure.json.tmp-old",
            ".result.worker-scratch-old",
        )
        for name in names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "result"
                stale = root / name
                stale.mkdir()
                with self.assertRaisesRegex(ValueError, "stale or peer temp/scratch"):
                    ev._new_staging(output)
                with self.assertRaisesRegex(ValueError, "stale or peer temp/scratch"):
                    ev._atomic_failure(output, "race", RuntimeError("boom"), True)
                self.assertTrue(stale.is_dir())
                self.assertFalse(os.path.lexists(output))
                self.assertFalse(os.path.lexists(ev._failure_path(output)))
                self.assertFalse(os.path.lexists(ev._namespace_claim_path(output)))
                self.assertEqual([path.name for path in root.iterdir()], [name])

    def test_raced_exact_token_staging_is_preserved_and_never_claimed_as_own(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "result"
            original_mkdir = Path.mkdir
            raced: list[Path] = []

            def peer_creates(path: Path, *args, **kwargs):
                if path.parent == root and path.name.startswith(".result.tmp-"):
                    original_mkdir(path, *args, **kwargs)
                    (path / "peer.txt").write_text(
                        "peer-owned\n", encoding="utf-8", newline="\n")
                    raced.append(path)
                    raise FileExistsError("peer created exact token path")
                return original_mkdir(path, *args, **kwargs)

            with mock.patch.object(Path, "mkdir", new=peer_creates):
                with self.assertRaisesRegex(FileExistsError, "peer created exact token"):
                    ev._new_staging(output)
            self.assertEqual(len(raced), 1)
            self.assertEqual((raced[0] / "peer.txt").read_text(encoding="utf-8"),
                             "peer-owned\n")
            self.assertFalse(os.path.lexists(output))
            self.assertFalse(os.path.lexists(ev._failure_path(output)))
            self.assertFalse(os.path.lexists(ev._namespace_claim_path(output)))

    def test_peer_transient_injected_after_staging_blocks_both_terminal_paths(self) -> None:
        for name in (".result.tmp-peer", ".result-result-review.json.tmp-peer",
                     ".result-failure.json.tmp-peer",
                     ".result.worker-scratch-peer"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "result"
                staging, lease = ev._new_staging(output)
                (staging / "payload.txt").write_text(
                    "candidate\n", encoding="utf-8", newline="\n")
                peer = root / name
                peer.mkdir()
                with self.assertRaisesRegex(ValueError, "stale or peer temp/scratch"):
                    ev._publish(staging, output, lease)
                shutil.rmtree(staging)
                with self.assertRaisesRegex(ValueError, "stale or peer temp/scratch"):
                    ev._atomic_failure(
                        output, "race", RuntimeError("boom"), True, lease=lease)
                self.assertTrue(peer.is_dir())
                self.assertFalse(os.path.lexists(output))
                self.assertFalse(os.path.lexists(ev._failure_path(output)))
                self.assertFalse(os.path.lexists(ev._namespace_claim_path(output)))
                self.assertEqual([path.name for path in root.iterdir()], [name])

    def test_failure_publication_rechecks_linked_parent_and_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / "outside"
            target.mkdir()
            linked_parent = base / "linked-parent"
            linked_created = False
            try:
                os.symlink(target, linked_parent, target_is_directory=True)
                linked_created = True
            except OSError:
                linked_parent.mkdir()
            output = linked_parent / "result"
            if linked_created:
                with self.assertRaisesRegex(ValueError, "linked/reparse"):
                    ev._atomic_failure(output, "race", RuntimeError("boom"), True)
            else:
                detector = ev._is_link_or_reparse

                def linked_detector(path: Path) -> bool:
                    return Path(path) == linked_parent or detector(path)

                with mock.patch.object(ev, "_is_link_or_reparse",
                                       side_effect=linked_detector):
                    with self.assertRaisesRegex(ValueError, "linked/reparse"):
                        ev._atomic_failure(output, "race", RuntimeError("boom"), True)
            self.assertFalse(os.path.lexists(target / "result"))
            self.assertFalse(os.path.lexists(target / "result-failure.json"))
            self.assertFalse(os.path.lexists(target / ".result.publication-claim"))
            self.assertFalse(list(target.glob(".*.tmp-*")))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "parent" / "result"
            output.parent.mkdir()
            original = ev.require_unlinked_path

            def reject_final_parent(path: Path, label: str, *, directory: bool = False) -> Path:
                if label == "evaluation failure parent":
                    raise ValueError("linked/reparse path forbidden after swap")
                return original(path, label, directory=directory)

            with mock.patch.object(ev, "require_unlinked_path", side_effect=reject_final_parent):
                with self.assertRaisesRegex(ValueError, "linked/reparse"):
                    ev._atomic_failure(output, "race", RuntimeError("boom"), True)
            self.assertFalse(os.path.lexists(output))
            self.assertFalse(os.path.lexists(ev._failure_path(output)))
            self.assertFalse(os.path.lexists(ev._namespace_claim_path(output)))
            self.assertFalse(list(output.parent.glob(".*.tmp-*")))


if __name__ == "__main__":
    unittest.main()
