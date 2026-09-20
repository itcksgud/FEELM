"""Independent fail-closed verifier for FM-v5 handoff and run artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from fm_v2_train import file_pin


ROOT = Path(__file__).resolve().parents[1]


def require(value: bool, message: str, checks: list[str]) -> None:
    if not value:
        raise RuntimeError(message)
    checks.append(message)


def verify_handoff(root: Path, config: dict, checks: list[str]) -> None:
    execution = config["execution_input"]
    handoff = execution["handoff"]
    episode = root / "input/episodes.train-validation.jsonl"
    candidate = root / "input/candidates.train-validation.jsonl"
    require(file_pin(episode)["sha256"] == handoff["train_validation_episode_file_sha256"],
            "handoff episode raw hash matches", checks)
    require(file_pin(candidate)["sha256"] == handoff["train_validation_candidate_file_sha256"],
            "handoff candidate raw hash matches", checks)
    for path in (episode, candidate):
        with path.open("rb") as stream:
            require(not any(b"FINAL_TEST" in block for block in iter(lambda: stream.read(8 * 1024 * 1024), b"")),
                    f"{path.name} excludes FINAL_TEST token", checks)
    lineage = json.loads((root / "metadata/handoff-lineage.json").read_text(encoding="utf-8"))
    seal = json.loads((root / "metadata/FINAL_TEST-SEAL.json").read_text(encoding="utf-8"))
    require(lineage["source_full_contract_digests"] == {
        "episodes": execution["source_episode_sha256"],
        "candidates": execution["source_candidate_sha256"],
        "distribution": execution["source_distribution_report_sha256"],
    }, "handoff source-wide logical digests match config", checks)
    require(lineage["source_bundle"]["digest"] == handoff["source_bundle_digest"],
            "handoff source bundle digest matches config", checks)
    require(seal["seal_id"] == handoff["final_test_seal_id"] and seal["content"] == "NOT_INCLUDED",
            "handoff FINAL_TEST remains sealed and absent", checks)


def _verify_file_map(root: Path, files: dict, checks: list[str], label: str) -> None:
    for name, expected in files.items():
        require((root / name).is_file() and file_pin(root / name) == expected,
                f"{label} file pin matches: {name}", checks)


def verify_prepared(prepared: Path, labels: Path, config: dict, checks: list[str]) -> None:
    manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == 5 and manifest.get("status") == "PASS" and
            manifest.get("failed_rows") == 0, "prepared manifest is schema-v5 PASS", checks)
    require(manifest.get("validation_labels") == "SEPARATE_UNMOUNTED_ARTIFACT" and
            manifest.get("validation_labels_opened") is False and manifest.get("final_test_opened") is False,
            "prepared manifest preserves label and FINAL_TEST firewalls", checks)
    _verify_file_map(prepared, manifest["files"], checks, "prepared")
    label_manifest = json.loads((labels / "manifest.json").read_text(encoding="utf-8"))
    require(label_manifest.get("status") == "SEALED_VALIDATION_LABELS" and
            label_manifest.get("final_test") == "NOT_PRESENT", "label manifest contains validation only", checks)
    _verify_file_map(labels, label_manifest["files"], checks, "label")
    require(manifest["row_key_multisets"]["validation_targets"] ==
            manifest["row_key_multisets"]["target_labels"], "target feature/label key multisets match", checks)
    require(manifest["row_key_multisets"]["validation_candidates"] ==
            manifest["row_key_multisets"]["candidate_labels"], "candidate feature/label key multisets match", checks)
    for name in ("validation-targets.parquet", "validation-candidates.parquet"):
        columns = set(pq.ParquetFile(prepared / name).schema_arrow.names)
        require(not ({"label", "label_state", "observed_rating"} & columns),
                f"{name} exposes no validation labels", checks)
    schema = json.loads((prepared / "feature-schema.json").read_text(encoding="utf-8"))
    factor_indices = set()
    for profile in ("sparse_history_fm_v5", "sparse_history_content_fm_v5"):
        factor_indices.update(schema["profiles"][profile]["positive_factor_indices"])
        factor_indices.update(schema["profiles"][profile]["negative_factor_indices"])
    violations = 0
    for batch in pq.ParquetFile(prepared / "train-targets.parquet").iter_batches(columns=["n", "feature_indices"]):
        for n, indices in zip(batch.column(0).to_pylist(), batch.column(1).to_pylist()):
            if int(n) == 0 and factor_indices.intersection(indices):
                violations += 1
    require(violations == 0, "N=0 activates no history factor value", checks)
    source_files = manifest["source_bundle"]["files"]
    require({name: file_pin(ROOT / name) for name in source_files} == source_files,
            "prepared source bundle matches executing workspace", checks)


def verify_fits(fits: Path, prepared: Path, checks: list[str]) -> None:
    report = json.loads((fits / "run-report.json").read_text(encoding="utf-8"))
    require(report.get("schema_version") == 5 and report.get("status") == "PASS",
            "fit report is schema-v5 PASS", checks)
    require(report.get("validation_labels_read") is False and report.get("final_test_opened") is False,
            "fit opened neither validation labels nor FINAL_TEST", checks)
    require(report["prepared_manifest"] == file_pin(prepared / "manifest.json"),
            "fit pins exact prepared manifest", checks)
    for name in ("validation-target-predictions.parquet", "validation-candidate-predictions.parquet"):
        table = pq.ParquetFile(fits / name)
        require(table.metadata.num_rows > 0, f"{name} is non-empty", checks)
        require(not ({"label", "label_state", "observed_rating"} & set(table.schema_arrow.names)),
                f"{name} contains no labels", checks)


def verify_evaluation(path: Path, checks: list[str]) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    require(report.get("schema_version") == 5 and report.get("status") == "PASS",
            "evaluation is schema-v5 PASS", checks)
    require(report.get("claim_ceiling") == "CANDIDATE" and
            report.get("K_POLICY_STATUS") != "VALIDATED" and
            report.get("TRANSITION_THRESHOLD_STATUS") != "VALIDATED",
            "evaluation enforces the preregistered CANDIDATE ceiling", checks)
    require(report.get("validation_labels_opened") is True and report.get("final_test_opened") is False,
            "evaluation opens validation only and keeps FINAL_TEST sealed", checks)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path)
    parser.add_argument("--labels-root", type=Path)
    parser.add_argument("--fits-root", type=Path)
    parser.add_argument("--evaluation", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    checks: list[str] = []
    verify_handoff(args.handoff_root, config, checks)
    if bool(args.prepared_root) != bool(args.labels_root):
        raise RuntimeError("prepared and labels roots must be supplied together")
    if args.prepared_root:
        verify_prepared(args.prepared_root, args.labels_root, config, checks)
    if args.fits_root:
        if not args.prepared_root:
            raise RuntimeError("fit verification requires prepared root")
        verify_fits(args.fits_root, args.prepared_root, checks)
    if args.evaluation:
        verify_evaluation(args.evaluation, checks)
    receipt = {"schema_version": 5, "status": "PASS", "checks": checks,
               "check_count": len(checks), "final_test_opened": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
