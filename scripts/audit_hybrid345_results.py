"""Independent, fail-closed audit of the completed hybrid345 experiment.

This module intentionally does not import ``hybrid345_evaluate`` or any of its
metric functions.  The command line entry point verifies producer, evaluation,
catalogue, report, and code-review lineage before it opens the frozen labels or
any label-derived result table.  It then recomputes the principal aggregates
from the lowest-level published result tables.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recommendation/experiments/hybrid345"
OUT = ROOT / "outputs/recommendation-evidence/hybrid345"
RUNTIME = ROOT / "outputs/recommendation-evidence/hybrid345-runtime"
CONFIG = DOC / "config.json"
RESULT = DOC / "RESULT.md"
PRELABEL_REVIEW = DOC / "prelabel-code-review.json"
EVALUATION_REVIEW = DOC / "evaluation-code-review.json"
CATALOG_REVIEW = DOC / "catalog-code-review.json"
AUDIT_REVIEW = DOC / "result-audit-code-review.json"
AUDIT_OUTPUT = OUT / "result-audit.json"
TMDB_VOTE_METADATA_RELATIVE = "outputs/recommendation-evidence/rec-ev-045/metadata.parquet"
FINAL344_CALIBRATION_RELATIVE = "outputs/recommendation-evidence/final344/final-calibration.json"
FINAL344_CALIBRATION_SEAL_RELATIVE = "outputs/recommendation-evidence/final344/final-calibration-seal.json"

PRELABEL_FILES = (
    "scripts/hybrid345_common.py",
    "scripts/hybrid345_documents.py",
    "scripts/hybrid345_encode.py",
    "scripts/hybrid345_models.py",
    "scripts/hybrid345_score.py",
    "scripts/test_hybrid345_documents.py",
    "scripts/test_hybrid345_encode.py",
    "scripts/test_hybrid345_models.py",
    "scripts/test_hybrid345_score.py",
    "docs/recommendation/experiments/hybrid345/DESIGN.md",
    "docs/recommendation/experiments/hybrid345/config.json",
    "docs/recommendation/experiments/hybrid345/design-review.json",
)
EVALUATION_FILES = (
    "scripts/hybrid345_evaluate.py",
    "scripts/report_hybrid345.py",
    "scripts/test_hybrid345_evaluate.py",
    "docs/recommendation/experiments/hybrid345/DESIGN.md",
    "docs/recommendation/experiments/hybrid345/config.json",
    "docs/recommendation/experiments/hybrid345/design-review.json",
)
CATALOG_FILES = (
    "scripts/hybrid345_catalog.py",
    "scripts/test_hybrid345_catalog.py",
    "scripts/report_hybrid345.py",
    "scripts/hybrid345_evaluate.py",
    "scripts/test_hybrid345_evaluate.py",
    "docs/recommendation/experiments/hybrid345/DESIGN.md",
    "docs/recommendation/experiments/hybrid345/config.json",
    "docs/recommendation/experiments/hybrid345/design-review.json",
    "docs/recommendation/experiments/hybrid345/evaluation-code-review.json",
)
AUDIT_FILES = (
    "scripts/audit_hybrid345_results.py",
    "scripts/test_audit_hybrid345_results.py",
    "scripts/hybrid345_evaluate.py",
    "scripts/test_hybrid345_evaluate.py",
    "scripts/hybrid345_catalog.py",
    "scripts/test_hybrid345_catalog.py",
    "scripts/report_hybrid345.py",
    "docs/recommendation/experiments/hybrid345/DESIGN.md",
    "docs/recommendation/experiments/hybrid345/config.json",
    "docs/recommendation/experiments/hybrid345/prelabel-code-review.json",
    "docs/recommendation/experiments/hybrid345/evaluation-code-review.json",
    "docs/recommendation/experiments/hybrid345/catalog-code-review.json",
)
CORE_METRICS = (
    "mse", "mae", "bias", "pa",
    "ndcg1", "ndcg2", "ndcg4", "ndcg6",
    "stars1", "stars2", "stars4", "stars6",
    "good1", "good2", "good4", "good6",
    "low1", "low2", "low4", "low6",
    "any_low1", "any_low2", "any_low4", "any_low6",
    "both_low1", "both_low2", "both_low4", "both_low6",
)
BASE_MODELS = (
    "ALS", "STRUCTURED_DIRECT", "E5_DIRECT", "QWEN_DIRECT",
    "FM150_s339", "FM150_s344", "FM150_s345",
    "GBT120_s339", "GBT120_s344", "GBT120_s345", "ALS_C2F",
)
DERIVED_MODELS = ("ALS_QWEN", "ROUTER_s339")
SEED_MEAN_MODELS = ("FM150_SEED_MEAN", "GBT120_SEED_MEAN")
ALL_USER_MODELS = (*BASE_MODELS, *DERIVED_MODELS, *SEED_MEAN_MODELS)
GROUPS = ("ALL", "W_DIRECT", "C", "V", "E", "NATURAL_ZERO")
CATALOG_MODELS = (
    "QWEN_DIRECT", "ALS_C2F", "ALS_QWEN", "SELECTED_COLD_HEAD", "ROUTER_s339",
)
TOP_NS = (2, 4, 6)
ATOL = 1e-12


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pin(path: Path) -> dict[str, Any]:
    path = Path(path)
    require(path.is_file(), f"missing file: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
            size += len(block)
    return {"bytes": size, "sha256": digest.hexdigest()}


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def assert_pin(path: Path, expected: Mapping[str, Any], label: str) -> None:
    require(isinstance(expected, Mapping), f"invalid pin for {label}")
    require(pin(path) == {"bytes": int(expected["bytes"]), "sha256": str(expected["sha256"])},
            f"sealed file drift: {label}")


def load_locked_tmdb_vote_metadata(
    config: Mapping[str, Any],
    catalog: pd.DataFrame | None = None,
    root: Path = ROOT,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    """Independently follow final344's pinned input lock to REC-045 votes."""
    root = Path(root)
    sources = config.get("sources", {})
    source_pins = config.get("source_pins", {})
    input_lock_relative = str(sources.get("final344_input_lock", ""))
    require(input_lock_relative in source_pins, "final344 input-lock config pin")
    input_lock_path = root / input_lock_relative
    assert_pin(input_lock_path, source_pins[input_lock_relative], "config final344 input lock")
    input_lock = read_json(input_lock_path)
    require(input_lock.get("evaluation_scope") == "DEVELOPMENT_ONLY" and
            input_lock.get("evaluation_labels_read") is False,
            "final344 label-free development input lock")
    expected = input_lock.get("files", {}).get(TMDB_VOTE_METADATA_RELATIVE)
    require(expected is not None, "final344 input lock REC-045 vote metadata pin")
    metadata_path = root / TMDB_VOTE_METADATA_RELATIVE
    assert_pin(metadata_path, expected, "REC-045 TMDB vote metadata")
    metadata = pd.read_parquet(metadata_path, columns=["movie_id", "tmdb_vote_count"])
    require(not metadata.movie_id.duplicated().any(), "unique REC-045 vote movie ids")
    if catalog is not None:
        require(np.array_equal(metadata.movie_id.to_numpy(np.int64),
                               catalog.movie_id.to_numpy(np.int64)),
                "REC-045 vote metadata/catalog movie axis")
    votes = metadata.tmdb_vote_count.to_numpy(np.float64)
    require(np.isfinite(votes).all() and (votes >= 0).all() and np.equal(votes, np.floor(votes)).all(),
            "REC-045 vote counts are finite nonnegative integers")
    return metadata, {
        "final344_input_lock": pin(input_lock_path),
        "tmdb_vote_metadata": pin(metadata_path),
    }


def fingerprint(paths: Sequence[str]) -> dict[str, dict[str, Any]]:
    return {name: pin(ROOT / name) for name in paths}


def verify_review(path: Path, paths: Sequence[str], label: str) -> dict[str, Any]:
    review = read_json(path)
    require(review.get("status") == "PASS", f"{label} review did not PASS")
    if label in {"evaluation", "catalog", "result audit"}:
        require(review.get("scope") == "DEVELOPMENT_ONLY", f"{label} review scope")
    require(review.get("fingerprint") == fingerprint(paths), f"{label} reviewed-code fingerprint drift")
    return review


def resolve_sealed(name: str, base: Path) -> Path:
    candidate = Path(name)
    if candidate.is_absolute():
        return candidate
    local = base / candidate
    return local if local.exists() else ROOT / candidate


def verify_pin_map(base: Path, values: Mapping[str, Any], label: str) -> None:
    require(isinstance(values, Mapping), f"{label} pin map")
    for name, expected in sorted(values.items()):
        assert_pin(resolve_sealed(str(name), base), expected, f"{label}:{name}")


def verify_implementation(values: Mapping[str, Any], expected: Mapping[str, Any], label: str) -> None:
    require(values == expected, f"{label} implementation fingerprint drift")


def verify_qwen_lineage(config: Mapping[str, Any], prelabel: Mapping[str, Any]) -> dict[str, Any]:
    expected_impl = {
        "scripts/hybrid345_common.py": pin(ROOT / "scripts/hybrid345_common.py"),
        "scripts/hybrid345_encode.py": pin(ROOT / "scripts/hybrid345_encode.py"),
        "docs/recommendation/experiments/hybrid345/config.json": pin(CONFIG),
    }
    document_impl = {
        "scripts/hybrid345_common.py": pin(ROOT / "scripts/hybrid345_common.py"),
        "scripts/hybrid345_documents.py": pin(ROOT / "scripts/hybrid345_documents.py"),
        "docs/recommendation/experiments/hybrid345/config.json": pin(CONFIG),
    }
    documents_path = OUT / "documents-seal.json"
    documents = read_json(documents_path)
    require(documents.get("status") == "PASS_LABEL_FREE_INPUTS_SEALED", "document seal status")
    require(documents.get("labels_opened") is False, "documents must be label-free")
    verify_pin_map(ROOT, documents.get("source_pins", {}), "document source")
    for name, expected in documents.get("source_pins", {}).items():
        require(config.get("source_pins", {}).get(name) == expected,
                f"document source no longer pinned by config: {name}")
    verify_pin_map(OUT, documents.get("artifacts", {}), "document artifact")
    verify_implementation(documents.get("implementation", {}), document_impl, "document")

    model_path = RUNTIME / "model-seal.json"
    model = read_json(model_path)
    require(model.get("status") == "PASS_PINNED_LOCAL_MODEL", "model seal status")
    require(model.get("model_id") == config["qwen"]["model_id"] and
            model.get("revision") == config["qwen"]["revision"], "model identity")
    model_dir = ROOT / str(model["local_path"])
    rows = model.get("files", [])
    require(rows, "model file inventory")
    sealed_names = {str(row["path"]) for row in rows}
    actual_names = {
        path.relative_to(model_dir).as_posix()
        for path in model_dir.rglob("*")
        if path.is_file() and ".cache" not in path.relative_to(model_dir).parts
    }
    require(actual_names == sealed_names, "Qwen model file inventory drift")
    for row in rows:
        assert_pin(model_dir / row["path"], row, f"Qwen model:{row['path']}")

    token_path = OUT / "qwen-token-audit-seal.json"
    token = read_json(token_path)
    require(token.get("status") == "PASS_ZERO_TRUNCATION", "token audit status")
    require(token.get("parents") == {
        "documents-seal.json": pin(documents_path), "model-seal.json": pin(model_path)},
        "token parent drift")
    verify_pin_map(OUT, token.get("artifacts", {}), "token artifact")
    verify_implementation(token.get("implementation", {}), expected_impl, "token")

    pilot_path = OUT / "qwen-pilot-seal.json"
    pilot = read_json(pilot_path)
    require(pilot.get("status") == "PASS", "pilot seal status")
    require(pilot.get("parents") == {
        "documents-seal.json": pin(documents_path),
        "model-seal.json": pin(model_path),
        "qwen-token-audit-seal.json": pin(token_path),
    }, "pilot parent drift")
    verify_pin_map(OUT, pilot.get("artifacts", {}), "pilot artifact")
    verify_implementation(pilot.get("implementation", {}), expected_impl, "pilot")

    pilot_review_path = DOC / "pilot-review.json"
    pilot_review = read_json(pilot_review_path)
    require(pilot_review.get("status") == "PASS" and
            pilot_review.get("pilot_seal") == pin(pilot_path), "pilot review drift")
    verify_implementation(pilot_review.get("implementation", {}), expected_impl, "pilot review")

    full_path = OUT / "qwen-embedding-seal.json"
    full = read_json(full_path)
    require(full.get("status") == "PASS" and full.get("labels_opened") is False,
            "full embedding seal status/scope")
    require(full.get("parents") == {
        "documents-seal.json": pin(documents_path),
        "model-seal.json": pin(model_path),
        "qwen-token-audit-seal.json": pin(token_path),
        "qwen-pilot-seal.json": pin(pilot_path),
        "pilot-review.json": pin(pilot_review_path),
    }, "full embedding parent drift")
    require(full.get("pilot_review") == pilot_review, "embedded pilot review drift")
    verify_pin_map(OUT, full.get("artifacts", {}), "full embedding artifact")
    verify_implementation(full.get("implementation", {}), expected_impl, "full embedding")
    require(prelabel.get("fingerprint") == fingerprint(PRELABEL_FILES), "prelabel fingerprint changed")
    return {"documents": pin(documents_path), "model": pin(model_path), "token": pin(token_path),
            "pilot": pin(pilot_path), "full": pin(full_path)}


def verify_als_factor_parent(parent: Mapping[str, Any]) -> None:
    fit_seal_path = ROOT / "outputs/recommendation-evidence/combination340/fit-seal.json"
    require(parent.get("fit_seal") == pin(fit_seal_path), "ALS factor fit seal drift")
    for name, expected in parent.get("files", {}).items():
        assert_pin(ROOT / "outputs/recommendation-evidence/combination340" / name,
                   expected, f"ALS factor:{name}")


def verify_final344_seal(path: Path, input_lock_path: Path) -> dict[str, Any]:
    """Independently verify a final344 seal's declared artifacts and code pins."""
    record = read_json(path)
    if path != input_lock_path:
        require(record.get("input_lock") == pin(input_lock_path),
                f"final344 input-lock parent: {path.name}")
    verify_pin_map(path.parent, record.get("files", {}), f"final344 {path.name}")
    execution = record.get("execution", {})
    if execution:
        verify_pin_map(ROOT, execution, f"final344 execution {path.name}")
    return record


def verify_final344_calibration_parent(
    config: Mapping[str, Any],
    root: Path = ROOT,
) -> dict[str, tuple[Path, dict[str, Any]]]:
    """Independently bind the prior calibration reference to the fixed input lock."""
    root = Path(root)
    input_lock_relative = str(config.get("sources", {}).get("final344_input_lock", ""))
    expected_input_lock = config.get("source_pins", {}).get(input_lock_relative)
    require(expected_input_lock is not None, "final344 calibration input-lock config pin")
    input_lock_path = root / input_lock_relative
    assert_pin(input_lock_path, expected_input_lock, "final344 calibration input lock")
    seal_path = root / FINAL344_CALIBRATION_SEAL_RELATIVE
    calibration_path = root / FINAL344_CALIBRATION_RELATIVE
    seal = read_json(seal_path)
    require(seal.get("input_lock") == pin(input_lock_path),
            "final344 calibration seal/input-lock lineage")
    require(set(seal.get("files", {})) == {"final-calibration.json"},
            "final344 calibration exact file inventory")
    assert_pin(calibration_path, seal["files"]["final-calibration.json"],
               "final344 calibration reference")
    execution = seal.get("execution", {})
    require(isinstance(execution, Mapping), "final344 calibration execution pin map")
    verify_pin_map(root, execution, "final344 calibration execution")
    return {
        "final344_calibration_seal": (seal_path, pin(seal_path)),
        "final344_calibration": (calibration_path, pin(calibration_path)),
    }


def verify_prediction_lineage(config: Mapping[str, Any], prelabel: Mapping[str, Any]) -> dict[str, Any]:
    qwen = verify_qwen_lineage(config, prelabel)
    expected_prelabel = fingerprint(PRELABEL_FILES)

    prior_path = OUT / "prior-seal.json"
    prior = read_json(prior_path)
    require(prior.get("scope") == "DEVELOPMENT_ONLY" and prior.get("labels_opened") is False,
            "prior label-free scope")
    assert_pin(OUT / "current-prior.npz", prior["file"], "prior artifact")
    assert_pin(ROOT / config["sources"]["training_ratings"], prior["source"], "prior source")
    verify_implementation(prior.get("implementation", {}), expected_prelabel, "prior")
    require(prior.get("independent_review") == pin(PRELABEL_REVIEW), "prior review drift")

    mapper_path = OUT / "mapper-seal.json"
    mapper = read_json(mapper_path)
    require(mapper.get("scope") == "DEVELOPMENT_ONLY" and mapper.get("labels_opened") is False,
            "mapper label-free scope")
    verify_pin_map(OUT, mapper.get("files", {}), "mapper artifact")
    mapper_source_paths = {
        "config": CONFIG,
        "embedding": OUT / "qwen-embeddings.npy",
    }
    require(set(mapper.get("sources", {})) == set(mapper_source_paths), "mapper source inventory")
    for name, path in mapper_source_paths.items():
        assert_pin(path, mapper["sources"][name], f"mapper source:{name}")
    require(mapper.get("qwen_parent") == pin(OUT / "qwen-embedding-seal.json"),
            "mapper Qwen parent")
    verify_als_factor_parent(mapper.get("als_factor_parent", {}))
    verify_implementation(mapper.get("implementation", {}), expected_prelabel, "mapper")
    require(mapper.get("independent_review") == pin(PRELABEL_REVIEW), "mapper review drift")

    prediction_path = OUT / "prediction-seal.json"
    prediction = read_json(prediction_path)
    require(prediction.get("scope") == "DEVELOPMENT_ONLY" and
            prediction.get("labels_opened") is False, "prediction label-free scope")
    verify_pin_map(OUT, prediction.get("files", {}), "prediction artifact")
    sources = prediction.get("sources", {})
    prediction_source_paths = {
        "config": CONFIG,
        "embedding": OUT / "qwen-embeddings.npy",
        "mapper_seal": OUT / "mapper-seal.json",
        "catalog": ROOT / config["sources"]["catalog"],
        "contexts": ROOT / config["sources"]["contexts"],
        "structured": ROOT / config["sources"]["structured"],
        "e5": ROOT / config["sources"]["e5"],
        "prior": ROOT / config["sources"]["prior"],
        "als_predictions": ROOT / config["sources"]["als_predictions"],
    }
    require(set(sources) == {*prediction_source_paths, "fm_predictions", "gbt_predictions"},
            "prediction source inventory")
    for name, expected in sources.items():
        if name in {"fm_predictions", "gbt_predictions"}:
            configured = config["sources"]["fm_predictions_by_seed" if name.startswith("fm") else "gbt_predictions_by_seed"]
            require(set(expected) == set(configured), f"{name} seed inventory")
            for seed, value in expected.items():
                assert_pin(ROOT / configured[str(seed)], value, f"{name}:{seed}")
        else:
            assert_pin(prediction_source_paths[name], expected, f"prediction source:{name}")
    require(prediction.get("mapper_parent") == mapper, "prediction embedded mapper parent drift")
    require(prediction.get("prior_parent") == prior, "prediction embedded prior parent drift")
    require(prediction.get("qwen_parent") == read_json(OUT / "qwen-embedding-seal.json"),
            "prediction embedded Qwen parent drift")
    verify_als_factor_parent(prediction.get("als_factor_parent", {}))
    verify_implementation(prediction.get("implementation", {}), expected_prelabel, "prediction")
    require(prediction.get("independent_review") == pin(PRELABEL_REVIEW), "prediction review drift")
    return {"seal": prediction, "pin": pin(prediction_path), "qwen": qwen,
            "prior": pin(prior_path), "mapper": pin(mapper_path)}


def verify_all_gates(output_dir: Path, result_path: Path) -> dict[str, Any]:
    """Verify all immutable lineage before any label/result-table read."""
    config = read_json(CONFIG)
    require(config.get("claim_scope") == "DEVELOPMENT_ONLY", "config scope")
    design_review = read_json(DOC / "design-review.json")
    require(design_review.get("status") == "PASS" and
            design_review.get("claim_scope") == "DEVELOPMENT_ONLY", "design review scope/status")
    require(design_review.get("reviewed_artifacts") == {
        "DESIGN.md": pin(DOC / "DESIGN.md"), "config.json": pin(CONFIG)},
        "design reviewed-artifact drift")
    prelabel = verify_review(PRELABEL_REVIEW, PRELABEL_FILES, "prelabel")
    evaluation_review = verify_review(EVALUATION_REVIEW, EVALUATION_FILES, "evaluation")
    catalog_review = verify_review(CATALOG_REVIEW, CATALOG_FILES, "catalog")
    audit_review = verify_review(AUDIT_REVIEW, AUDIT_FILES, "result audit")
    prediction = verify_prediction_lineage(config, prelabel)

    evaluation_seal_path = output_dir / "evaluation-seal.json"
    evaluation_seal = read_json(evaluation_seal_path)
    require(evaluation_seal.get("scope") == "DEVELOPMENT_ONLY", "evaluation seal scope")
    require(evaluation_seal.get("prediction_seal") == prediction["pin"], "evaluation prediction parent")
    expected_evaluation_files = {
        "calibration.json", "calibration-reproduction.json", "selection.json",
        "selection-user-metrics.parquet", "user-metrics.parquet", "page-metrics.parquet",
        "row-errors.parquet", "summary.csv", "page-summary.csv", "denominators.csv",
        "diagnostic-summary.csv", "primary-contrasts.csv", "primary-paired-users.parquet",
        "decisions.json", "evaluation-manifest.json",
    }
    require(set(evaluation_seal.get("files", {})) == expected_evaluation_files,
            "evaluation sealed output inventory")
    verify_pin_map(output_dir, evaluation_seal.get("files", {}), "evaluation output")
    manifest_path = output_dir / "evaluation-manifest.json"
    assert_pin(manifest_path, evaluation_seal["manifest"], "evaluation manifest")
    evaluation_manifest = read_json(manifest_path)
    require(evaluation_manifest.get("scope") == "DEVELOPMENT_ONLY", "evaluation manifest scope")
    require(evaluation_manifest.get("prediction_seal") == prediction["pin"], "manifest prediction parent")
    require(evaluation_manifest.get("evaluation_review") == pin(EVALUATION_REVIEW) and
            evaluation_manifest.get("evaluation_fingerprint") == evaluation_review["fingerprint"],
            "evaluation review lineage")
    require(evaluation_manifest.get("config") == pin(CONFIG), "evaluation config pin")
    evaluation_input_paths = {
        name: ROOT / config["sources"][name]
        for name in ("catalog", "contexts", "labels", "roles", "partition", "rec033_metadata", "texts",
                     "final344_input_lock")
    }
    vote_metadata, _ = load_locked_tmdb_vote_metadata(config)
    del vote_metadata
    evaluation_input_paths["tmdb_vote_metadata"] = ROOT / TMDB_VOTE_METADATA_RELATIVE
    calibration_inputs = verify_final344_calibration_parent(config)
    for name, (path, _) in calibration_inputs.items():
        evaluation_input_paths[name] = path
    require(set(evaluation_manifest.get("inputs", {})) == set(evaluation_input_paths),
            "evaluation input inventory")
    for name, path in evaluation_input_paths.items():
        assert_pin(path, evaluation_manifest["inputs"][name], f"evaluation input:{name}")
    require(set(evaluation_manifest.get("code", {})) == {"evaluation"}, "evaluation code inventory")
    assert_pin(ROOT / "scripts/hybrid345_evaluate.py", evaluation_manifest["code"]["evaluation"],
               "evaluation code")
    verify_pin_map(output_dir, evaluation_manifest.get("files", {}), "evaluation manifest output")

    catalog_seal_path = output_dir / "catalog-diagnostic-seal.json"
    catalog_seal = read_json(catalog_seal_path)
    require(catalog_seal.get("scope") == "DEVELOPMENT_ONLY" and
            catalog_seal.get("diagnostic_only") is True, "catalog diagnostic scope")
    require(catalog_seal.get("evaluation_seal") == pin(evaluation_seal_path) and
            catalog_seal.get("evaluation_manifest") == pin(manifest_path), "catalog evaluation parents")
    require(catalog_seal.get("selection") == pin(output_dir / "selection.json") and
            catalog_seal.get("calibration") == pin(output_dir / "calibration.json"),
            "catalog selection/calibration parents")
    require(catalog_seal.get("catalog_review") == pin(CATALOG_REVIEW) and
            catalog_seal.get("catalog_fingerprint") == catalog_review["fingerprint"],
            "catalog review lineage")
    require(catalog_seal.get("prediction_seal") == prediction["pin"] and
            catalog_seal.get("mapper_seal") == prediction["mapper"], "catalog producer parents")
    assert_pin(OUT / "qwen-embeddings.npy", catalog_seal["qwen_embeddings"], "catalog Qwen embedding")
    assert_pin(OUT / "mapped-factors.npy", catalog_seal["mapped_factors"], "catalog mapped factors")
    require(set(catalog_seal.get("files", {})) == {
        "catalog-top6.parquet", "catalog-summary.csv", "catalog-timing.csv", "catalog-supply.csv"},
        "catalog sealed output inventory")
    verify_pin_map(output_dir, catalog_seal.get("files", {}), "catalog output")
    assert_pin(ROOT / "scripts/hybrid345_catalog.py", catalog_seal["code"], "catalog code")

    final344_path = ROOT / config["sources"]["final344_catalog_seal"]
    require(catalog_seal.get("final344_catalog_seal") == pin(final344_path), "final344 catalog parent")
    final344 = read_json(final344_path)
    verify_pin_map(final344_path.parent, final344.get("files", {}), "final344 catalog cache")
    input_lock_path = ROOT / config["sources"]["final344_input_lock"]
    verify_final344_seal(input_lock_path, input_lock_path)
    for key in ("input_lock", "model_audit", "selection"):
        if key in final344:
            path = {
                "input_lock": ROOT / config["sources"]["final344_input_lock"],
                "model_audit": ROOT / config["sources"]["final344_model_audit"],
                "selection": final344_path.parent / "selection-seal.json",
            }[key]
            assert_pin(path, final344[key], f"final344:{key}")
    fit_paths = {
        **{f"FM150_s{seed}": ROOT / path for seed, path in config["sources"]["fm_seals_by_seed"].items()},
        **{f"GBT120_s{seed}": ROOT / path for seed, path in config["sources"]["gbt_seals_by_seed"].items()},
    }
    require(set(final344.get("fit_seals", {})) == set(fit_paths), "final344 fit-seal inventory")
    for name, path in fit_paths.items():
        assert_pin(path, final344["fit_seals"][name], f"final344 fit seal:{name}")
        verify_final344_seal(path, input_lock_path)
    selection_seal_path = final344_path.parent / "selection-seal.json"
    selection_seal = read_json(selection_seal_path)
    require(set(selection_seal.get("files", {})) ==
            {"selection.json", "selection-user-metrics.parquet"}, "final344 selection inventory")
    verify_pin_map(final344_path.parent, selection_seal["files"], "final344 selection")
    verify_final344_seal(selection_seal_path, input_lock_path)
    model_audit_path = ROOT / config["sources"]["final344_model_audit"]
    model_audit = read_json(model_audit_path)
    if "files" in model_audit:
        verify_pin_map(model_audit_path.parent, model_audit["files"], "final344 model audit")

    report_manifest_path = output_dir / "report-manifest.json"
    report_manifest = read_json(report_manifest_path)
    require(report_manifest.get("scope") == "DEVELOPMENT_ONLY", "report scope")
    require(report_manifest.get("evaluation_seal") == pin(evaluation_seal_path) and
            report_manifest.get("catalog_diagnostic_seal") == pin(catalog_seal_path),
            "report result parents")
    require(report_manifest.get("evaluation_review") == pin(EVALUATION_REVIEW) and
            report_manifest.get("evaluation_fingerprint") == evaluation_review["fingerprint"],
            "report review lineage")
    assert_pin(ROOT / "scripts/report_hybrid345.py", report_manifest["script"], "report code")
    assert_pin(result_path, report_manifest["result"], "RESULT.md")
    assert_pin(DOC / "comparison.png", report_manifest["plot"], "report plot")
    return {
        "config": config,
        "reviews": {"prelabel": pin(PRELABEL_REVIEW), "evaluation": pin(EVALUATION_REVIEW),
                    "catalog": pin(CATALOG_REVIEW), "audit": pin(AUDIT_REVIEW)},
        "seals": {"prediction": prediction["pin"], "evaluation": pin(evaluation_seal_path),
                  "catalog": pin(catalog_seal_path), "report": pin(report_manifest_path)},
        "evaluation_manifest": evaluation_manifest,
        "catalog_seal": catalog_seal,
        "report_manifest": report_manifest,
        "config_pin": pin(CONFIG),
        "review_fingerprints": {
            "prelabel": prelabel["fingerprint"],
            "evaluation": evaluation_review["fingerprint"],
            "catalog": catalog_review["fingerprint"],
            "audit": audit_review["fingerprint"],
        },
    }


def recompute_core_summary(users: pd.DataFrame) -> pd.DataFrame:
    required = {"model", "uid", "cap", "group", "h", "h_group", *CORE_METRICS}
    require(required.issubset(users.columns), "user-metrics schema")
    rows: list[dict[str, Any]] = []
    for keys, frame in users.groupby(["model", "cap", "group", "h_group"], sort=True):
        for metric in CORE_METRICS:
            values = frame[metric].dropna().astype(float)
            rows.append({"model": keys[0], "cap": int(keys[1]), "group": keys[2],
                         "h_group": keys[3], "metric": metric,
                         "valid_users": int(len(values)),
                         "user_contexts": int(len(frame)),
                         "mean": float(values.mean()) if len(values) else math.nan})
    return pd.DataFrame(rows)


def compare_core_summary(recomputed: pd.DataFrame, published: pd.DataFrame) -> None:
    keys = ["model", "cap", "group", "h_group", "metric"]
    expected = published[published.metric.isin(CORE_METRICS)].copy()
    require(not expected.duplicated(keys).any(), "published summary duplicate key")
    merged = recomputed.merge(expected, on=keys, how="outer", suffixes=("_audit", "_published"), indicator=True)
    require(merged._merge.eq("both").all(), "summary core-key mismatch")
    require((merged.valid_users_audit.astype(int) == merged.valid_users_published.astype(int)).all(),
            "summary valid-user denominator mismatch")
    require((merged.user_contexts_audit.astype(int) == merged.user_contexts_published.astype(int)).all(),
            "summary user-context denominator mismatch")
    left, right = merged.mean_audit.to_numpy(float), merged.mean_published.to_numpy(float)
    require(np.allclose(left, right, rtol=0.0, atol=ATOL, equal_nan=True), "summary mean mismatch")


def compare_denominators(users: pd.DataFrame, published: pd.DataFrame) -> None:
    keys = ["model", "cap", "group", "h_group"]
    rows = []
    for key, frame in users.groupby(keys, sort=True):
        rows.append(dict(zip(keys, key)) | {
            "user_contexts": len(frame), "complete_user_contexts": int(frame.complete.sum()),
            "target_rows": int(frame.targets_total.sum()), "scored_rows": int(frame.targets_scored.sum()),
        })
    actual = pd.DataFrame(rows)
    merged = actual.merge(published, on=keys, how="outer", suffixes=("_audit", "_published"), indicator=True)
    require(merged._merge.eq("both").all(), "denominator key mismatch")
    for name in ("user_contexts", "complete_user_contexts", "target_rows", "scored_rows"):
        require((merged[name + "_audit"].astype(int) == merged[name + "_published"].astype(int)).all(),
                f"denominator mismatch: {name}")


def stable_order(scores: np.ndarray, movie_ids: np.ndarray) -> np.ndarray:
    scores, movie_ids = np.asarray(scores, float), np.asarray(movie_ids, np.int64)
    require(scores.ndim == movie_ids.ndim == 1 and len(scores) == len(movie_ids), "ranking axes")
    require(np.isfinite(scores).all() and len(np.unique(movie_ids)) == len(movie_ids),
            "finite ranking scores and unique movies")
    return np.lexsort((movie_ids, -scores))


def independent_ndcg(y: np.ndarray, scores: np.ndarray, movie_ids: np.ndarray, n: int) -> float:
    if len(y) < n:
        return math.nan
    order = stable_order(scores, movie_ids)
    gains = (np.asarray(y, float) - 0.5) / 4.5
    discount = 1.0 / np.log2(np.arange(n, dtype=float) + 2.0)
    ideal = float(np.sort(gains)[::-1][:n] @ discount)
    return float(gains[order[:n]] @ discount / ideal) if ideal > 0 else math.nan


def independent_top(y: np.ndarray, scores: np.ndarray, movie_ids: np.ndarray,
                    end: int, start: int = 0) -> dict[str, float]:
    empty = {name: math.nan for name in ("stars", "good", "low", "any_low", "both_low")}
    if len(y) < end:
        return empty
    shown = np.asarray(y, float)[stable_order(scores, movie_ids)[start:end]]
    low = shown <= 2.0
    return {"stars": float(shown.mean()), "good": float((shown >= 4.0).mean()),
            "low": float(low.mean()), "any_low": float(low.any()),
            "both_low": float(low.all()) if len(shown) == 2 else math.nan}


def independent_pa(y: np.ndarray, scores: np.ndarray) -> float:
    if len(y) < 2:
        return math.nan
    left, right = np.triu_indices(len(y), 1)
    dy = np.asarray(y, float)[left] - np.asarray(y, float)[right]
    keep = dy != 0
    if not keep.any():
        return math.nan
    ds = np.asarray(scores, float)[left][keep] - np.asarray(scores, float)[right][keep]
    products = dy[keep] * ds
    return float(np.where(products > 0, 1.0, np.where(products == 0, 0.5, 0.0)).mean())


def independent_metric_bundle(y: np.ndarray, ranking: np.ndarray,
                              calibrated_raw: np.ndarray, movie_ids: np.ndarray) -> dict[str, float]:
    error = np.clip(np.asarray(calibrated_raw, float), 0.5, 5.0) - np.asarray(y, float)
    values = {"mse": float(np.mean(error ** 2)), "mae": float(np.mean(np.abs(error))),
              "bias": float(np.mean(error)), "pa": independent_pa(y, ranking)}
    for n in (1, 2, 4, 6):
        values[f"ndcg{n}"] = independent_ndcg(y, ranking, movie_ids, n)
        values.update({f"{name}{n}": value
                       for name, value in independent_top(y, ranking, movie_ids, n).items()})
    return values


def expected_comparison_axis(config: Mapping[str, Any], catalog: pd.DataFrame) -> pd.DataFrame:
    roles = pd.read_csv(ROOT / config["sources"]["roles"])
    comparison = set(roles.loc[roles.role.eq("comparison"), "uid"].astype(int))
    contexts = read_json(ROOT / config["sources"]["contexts"])
    ids = catalog.movie_id.to_numpy(np.int64)
    rows = []
    for context_id, context in enumerate(contexts):
        uid = int(context["uid"])
        if uid not in comparison:
            continue
        ei = np.asarray(context["ei"], np.int64)
        start, stop = int(context["start"]), int(context["stop"])
        require(stop - start == len(ei), "context row range")
        rows.append(pd.DataFrame({"row_id": np.arange(start, stop, dtype=np.int64),
                                  "context_id": context_id, "uid": uid,
                                  "cap": int(context["cap"]), "h": int(context["h"]),
                                  "movie_id": ids[ei]}))
    return pd.concat(rows, ignore_index=True).sort_values("row_id").reset_index(drop=True)


def validate_and_enrich_row_errors(errors: pd.DataFrame, labels: pd.DataFrame,
                                   config: Mapping[str, Any]) -> pd.DataFrame:
    required = {"row_id", "context_id", "uid", "cap", "h", "movie_id", "model", "rating",
                "available", "ranking_score", "calibrated_raw", "calibrated", "error", "se", "ae",
                "primary_group", "cold_partition", "train_count", "support_band", "release_year",
                "release_band", "tmdb_vote_count", "vote_band"}
    require(required.issubset(errors.columns), "row-errors schema")
    require(set(errors.model.unique()) == set((*BASE_MODELS, *DERIVED_MODELS)), "row-errors model inventory")
    require(not errors.duplicated(["model", "row_id"]).any(), "row-errors unique model/row")

    catalog = pd.read_parquet(ROOT / config["sources"]["catalog"])
    partition = pd.read_parquet(ROOT / config["sources"]["partition"])
    metadata, _ = load_locked_tmdb_vote_metadata(config, catalog)
    texts = pd.read_parquet(ROOT / config["sources"]["texts"], columns=["movie_id", "release_date"])
    require(np.array_equal(catalog.movie_id.to_numpy(), partition.movie_id.to_numpy()) and
            np.array_equal(catalog.movie_id.to_numpy(), metadata.movie_id.to_numpy()) and
            np.array_equal(catalog.movie_id.to_numpy(), texts.movie_id.to_numpy()), "metadata catalog axes")
    expected_axis = expected_comparison_axis(config, catalog)
    axis_columns = ["row_id", "context_id", "uid", "cap", "h", "movie_id"]
    for model in (*BASE_MODELS, *DERIVED_MODELS):
        current = errors.loc[errors.model.eq(model), axis_columns].sort_values("row_id").reset_index(drop=True)
        pd.testing.assert_frame_equal(current, expected_axis, check_dtype=False, check_exact=True)

    indexed_labels = labels.set_index(["uid", "movie_id"]).rating
    keys = pd.MultiIndex.from_frame(errors[["uid", "movie_id"]])
    joined = indexed_labels.reindex(keys).to_numpy(float)
    require(np.isfinite(joined).all() and np.array_equal(joined, errors.rating.to_numpy(float)),
            "row-errors exact label join")
    require(np.allclose(np.clip(errors.calibrated_raw.to_numpy(float), 0.5, 5.0),
                        errors.calibrated.to_numpy(float), rtol=0, atol=0, equal_nan=True),
            "row-errors clipped calibration")
    available = errors.available.to_numpy(bool)
    require(np.array_equal(available, np.isfinite(errors.ranking_score.to_numpy(float))) and
            np.array_equal(available, np.isfinite(errors.calibrated_raw.to_numpy(float))),
            "row-errors availability/score finiteness")
    derived_error = errors.calibrated.to_numpy(float) - errors.rating.to_numpy(float)
    require(np.allclose(derived_error, errors.error, rtol=0, atol=ATOL, equal_nan=True) and
            np.allclose(derived_error ** 2, errors.se, rtol=0, atol=ATOL, equal_nan=True) and
            np.allclose(np.abs(derived_error), errors.ae, rtol=0, atol=ATOL, equal_nan=True),
            "row-errors error columns")

    movie_lookup = catalog.set_index("movie_id")
    movie_ids = errors.movie_id.to_numpy(np.int64)
    support = movie_lookup.loc[movie_ids, "train_count"].to_numpy(np.int64)
    blocked = movie_lookup.loc[movie_ids, "blocked"].to_numpy(bool)
    partitions = partition.set_index("movie_id").loc[movie_ids, "partition"].astype(str).to_numpy()
    primary = np.where(blocked, "C", np.where(support > 0, "W_DIRECT", "NATURAL_ZERO"))
    cold = np.where(partitions == "V", "V", np.where(partitions == "E", "E", ""))
    support_band = np.select([support == 0, support <= 9, support <= 49],
                             ["0", "1_9", "10_49"], default="50_PLUS")
    require(np.array_equal(errors.train_count.to_numpy(np.int64), support) and
            np.array_equal(errors.primary_group.astype(str), primary) and
            np.array_equal(errors.cold_partition.fillna("").astype(str), cold) and
            np.array_equal(errors.support_band.astype(str), support_band), "row-errors movie groups/support")

    dates = pd.to_datetime(texts.release_date, format="%Y-%m-%d", errors="coerce", utc=True)
    years_lookup = pd.Series(dates.dt.year.to_numpy(float), index=texts.movie_id)
    years = years_lookup.loc[movie_ids].to_numpy(float)
    release_band = np.select([~np.isfinite(years), years < 1980, years < 2000, years < 2010,
                              years < 2020, years < 2023],
                             ["MISSING", "PRE1980", "1980_1999", "2000_2009", "2010_2019", "2020_2022"],
                             default="2023_PLUS")
    votes = metadata.set_index("movie_id").loc[movie_ids, "tmdb_vote_count"].to_numpy(float)
    vote_band = np.select([~np.isfinite(votes) | (votes <= 0), votes <= 1, votes <= 9,
                           votes <= 49, votes <= 499],
                          ["ZERO_OR_MISSING", "ONE", "2_9", "10_49", "50_499"], default="500_PLUS")
    require(np.allclose(errors.release_year, years, rtol=0, atol=0, equal_nan=True) and
            np.array_equal(errors.release_band.astype(str), release_band) and
            np.allclose(errors.tmdb_vote_count, votes, rtol=0, atol=0, equal_nan=True) and
            np.array_equal(errors.vote_band.astype(str), vote_band), "row-errors diagnostic metadata")
    return errors


def average_seed_rows(frame: pd.DataFrame, page: bool = False) -> pd.DataFrame:
    keys = ["uid", "cap", "group", "h", "h_group"] + (["start", "end"] if page else [])
    constants = ["targets_total", "targets_scored", "complete"]
    identity = set([*keys, *constants, "model"])
    numeric = [name for name in frame.columns if name not in identity and pd.api.types.is_numeric_dtype(frame[name])]
    rows = []
    for prefix, output in (("FM150", "FM150_SEED_MEAN"), ("GBT120", "GBT120_SEED_MEAN")):
        names = {f"{prefix}_s{seed}" for seed in (339, 344, 345)}
        for _, group in frame.loc[frame.model.isin(names)].groupby(keys, sort=True, dropna=False):
            require(set(group.model) == names and len(group) == 3, "three seed rows")
            first = group.iloc[0]
            for name in constants:
                require(group[name].nunique(dropna=False) == 1, f"seed denominator agreement:{name}")
            row = {name: first[name] for name in [*keys, *constants]}
            row["model"] = output
            for name in numeric:
                values = group[name].to_numpy(float)
                row[name] = float(values.mean()) if np.isfinite(values).all() else math.nan
            rows.append(row)
    return pd.DataFrame(rows, columns=frame.columns)


def recompute_from_row_errors(errors: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    users, pages = [], []
    for (model, context_id), context in errors.groupby(["model", "context_id"], sort=True):
        require(context.uid.nunique() == context.cap.nunique() == context.h.nunique() == 1,
                "one context identity")
        for group in GROUPS:
            if group == "ALL":
                current = context
            elif group in {"V", "E"}:
                current = context[context.cold_partition.eq(group)]
            else:
                current = context[context.primary_group.eq(group)]
            if current.empty:
                continue
            complete = bool(current.available.all())
            row = {"model": model, "uid": int(current.uid.iloc[0]), "cap": int(current.cap.iloc[0]),
                   "group": group, "h": int(current.h.iloc[0]),
                   "targets_total": len(current), "targets_scored": int(current.available.sum()),
                   "complete": complete}
            if complete:
                row.update(independent_metric_bundle(current.rating.to_numpy(float),
                                                     current.ranking_score.to_numpy(float),
                                                     current.calibrated_raw.to_numpy(float),
                                                     current.movie_id.to_numpy(np.int64)))
            else:
                row.update({name: math.nan for name in CORE_METRICS})
            row["h_group"] = "H_POSITIVE" if row["h"] > 0 else "H_ZERO"
            users.append(row)
            for end in (2, 4, 6):
                values = independent_top(current.rating.to_numpy(float),
                                         current.ranking_score.to_numpy(float),
                                         current.movie_id.to_numpy(np.int64), end, end - 2) if complete else {
                                             name: math.nan for name in ("stars", "good", "low", "any_low", "both_low")}
                pages.append({"model": model, "uid": row["uid"], "cap": row["cap"], "group": group,
                              "h": row["h"], "start": end - 2, "end": end,
                              "targets_total": row["targets_total"], "targets_scored": row["targets_scored"],
                              "complete": complete, **values, "h_group": row["h_group"]})
    user_frame, page_frame = pd.DataFrame(users), pd.DataFrame(pages)
    user_frame = pd.concat([user_frame, average_seed_rows(user_frame)], ignore_index=True)
    page_frame = pd.concat([page_frame, average_seed_rows(page_frame, page=True)], ignore_index=True)
    return user_frame, page_frame


def compare_metric_table(actual: pd.DataFrame, published: pd.DataFrame, *, page: bool = False) -> None:
    keys = ["model", "uid", "cap", "group", "h", "h_group"] + (["start", "end"] if page else [])
    require(not actual.duplicated(keys).any() and not published.duplicated(keys).any(), "metric-table unique keys")
    merged = actual.merge(published, on=keys, how="outer", suffixes=("_audit", "_published"), indicator=True)
    require(merged._merge.eq("both").all(), "metric-table key mismatch")
    constants = ("targets_total", "targets_scored", "complete")
    for name in constants:
        require(np.array_equal(merged[name + "_audit"].to_numpy(), merged[name + "_published"].to_numpy()),
                f"metric-table field mismatch: {name}")
    metrics = ("stars", "good", "low", "any_low", "both_low") if page else CORE_METRICS
    for name in metrics:
        require(np.allclose(merged[name + "_audit"].to_numpy(float),
                            merged[name + "_published"].to_numpy(float),
                            rtol=0, atol=ATOL, equal_nan=True), f"metric-table value mismatch: {name}")


def summarize_pages_independent(pages: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for keys, frame in pages.groupby(["model", "cap", "group", "h_group", "start", "end"], sort=True):
        for metric in ("stars", "good", "low", "any_low", "both_low"):
            values = frame[metric].dropna()
            rows.append(dict(zip(["model", "cap", "group", "h_group", "start", "end"], keys)) |
                        {"metric": metric, "valid_users": len(values), "user_contexts": len(frame),
                         "mean": float(values.mean()) if len(values) else math.nan})
    return pd.DataFrame(rows)


def compare_summary_like(actual: pd.DataFrame, published: pd.DataFrame, keys: Sequence[str]) -> None:
    merged = actual.merge(published, on=list(keys), how="outer", suffixes=("_audit", "_published"), indicator=True)
    require(merged._merge.eq("both").all(), "summary-like key mismatch")
    for name in ("valid_users", "user_contexts"):
        require((merged[name + "_audit"].astype(int) == merged[name + "_published"].astype(int)).all(),
                f"summary-like denominator mismatch: {name}")
    require(np.allclose(merged.mean_audit, merged.mean_published, rtol=0, atol=ATOL, equal_nan=True),
            "summary-like mean mismatch")


def recompute_diagnostics(errors: pd.DataFrame) -> pd.DataFrame:
    source = errors[errors.cap.eq(10) & errors.h.gt(0) & errors.available]
    levels = {
        "cold_partition": ["V", "E"],
        "support_band": ["0", "1_9", "10_49", "50_PLUS"],
        "release_band": ["MISSING", "PRE1980", "1980_1999", "2000_2009", "2010_2019", "2020_2022", "2023_PLUS"],
        "vote_band": ["ZERO_OR_MISSING", "ONE", "2_9", "10_49", "50_499", "500_PLUS"],
    }
    rows = []
    for dimension, values in levels.items():
        for model in (*BASE_MODELS, *DERIVED_MODELS):
            model_rows = source[source.model.eq(model)]
            for level in values:
                current = model_rows[model_rows[dimension].eq(level)]
                per_user = current.groupby("uid", sort=True).se.mean()
                rows.append({"model": model, "dimension": dimension, "level": level,
                             "users": len(per_user), "rows": len(current),
                             "movies": current.movie_id.nunique(),
                             "user_macro_mse": float(per_user.mean()) if len(per_user) else math.nan})
    return pd.DataFrame(rows)


def compare_diagnostics(actual: pd.DataFrame, published: pd.DataFrame) -> None:
    keys = ["model", "dimension", "level"]
    merged = actual.merge(published, on=keys, how="outer", suffixes=("_audit", "_published"), indicator=True)
    require(merged._merge.eq("both").all(), "diagnostic key mismatch")
    for name in ("users", "rows", "movies"):
        require((merged[name + "_audit"].astype(int) == merged[name + "_published"].astype(int)).all(),
                f"diagnostic denominator mismatch: {name}")
    require(np.allclose(merged.user_macro_mse_audit, merged.user_macro_mse_published,
                        rtol=0, atol=ATOL, equal_nan=True), "diagnostic MSE mismatch")


def assert_nested_equal(actual: Any, expected: Any, label: str) -> None:
    if isinstance(actual, Mapping) and isinstance(expected, Mapping):
        require(set(actual) == set(expected), f"{label} object keys")
        for key in actual:
            assert_nested_equal(actual[key], expected[key], f"{label}.{key}")
    elif isinstance(actual, list) and isinstance(expected, list):
        require(len(actual) == len(expected), f"{label} list length")
        for index, (left, right) in enumerate(zip(actual, expected)):
            assert_nested_equal(left, right, f"{label}[{index}]")
    elif isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        require((math.isnan(float(actual)) and math.isnan(float(expected))) or
                math.isclose(float(actual), float(expected), rel_tol=0, abs_tol=ATOL), f"{label} numeric value")
    else:
        require(actual == expected, f"{label} value")


def selection_candidate_summary(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {"users": int(len(frame))}
    for name in ("mse", "ndcg2", "stars2", "low2", "any_low2"):
        values = frame[name].dropna()
        result[name] = float(values.mean()) if len(values) else None
        result[name + "_users"] = int(len(values))
    return result


def pareto_safe(candidate: Mapping[str, Any], baseline: Mapping[str, Any],
                safety: Mapping[str, Any]) -> bool:
    if any(candidate.get(key) is None or baseline.get(key) is None
           for key in ("mse", "ndcg2", "stars2", "low2")):
        return False
    pareto = candidate["mse"] <= baseline["mse"] + ATOL and candidate["ndcg2"] >= baseline["ndcg2"] - ATOL
    strict = candidate["mse"] < baseline["mse"] - ATOL or candidate["ndcg2"] > baseline["ndcg2"] + ATOL
    safe = (candidate["stars2"] >= baseline["stars2"] - float(safety["max_top2_stars_loss"]) - ATOL and
            candidate["low2"] <= baseline["low2"] + float(safety["max_low2_increase"]) + ATOL)
    return bool(pareto and strict and safe)


def verify_selection_metrics(frame: pd.DataFrame, selection: Mapping[str, Any],
                             config: Mapping[str, Any]) -> dict[str, Any]:
    required = {"selection", "candidate", "uid", "h", "complete",
                 "common_selection_uid", "mse", "ndcg2", "stars2", "low2", "any_low2", "weight"}
    require(required.issubset(frame.columns), "selection-user-metrics schema")
    require(set(frame.selection) == {"WARM_WEIGHT", "COLD_HEAD"}, "selection metric families")
    safety = config["safety"]

    warm = frame[frame.selection.eq("WARM_WEIGHT")]
    warm_names = {str(float(value)) for value in config["hybrid_content_weights"]}
    require(set(warm.candidate.astype(str)) == warm_names, "warm candidate inventory")
    require(np.allclose(warm.weight.to_numpy(float), warm.candidate.astype(float).to_numpy(float),
                        rtol=0, atol=0), "warm candidate/weight identity")
    warm_eligible: set[int] | None = None
    for name in sorted(warm_names):
        eligible = set(warm.loc[warm.candidate.astype(str).eq(name) & warm.mse.notna() & warm.ndcg2.notna(), "uid"].astype(int))
        warm_eligible = eligible if warm_eligible is None else warm_eligible & eligible
    warm_uids = sorted(warm_eligible or set())
    require(np.array_equal(warm.common_selection_uid.to_numpy(bool), warm.uid.isin(warm_uids).to_numpy()),
            "warm common UID marker")
    warm_metrics = {name: selection_candidate_summary(
        warm[warm.candidate.astype(str).eq(name) & warm.common_selection_uid]) for name in sorted(warm_names, key=float)}
    warm_baseline = warm_metrics["0.0"]
    passed_weights = [float(name) for name in warm_metrics if float(name) > 0 and
                      pareto_safe(warm_metrics[name], warm_baseline, safety)]
    warm_choice = min(passed_weights) if passed_weights else 0.0
    expected_warm = {
        "content_weight": warm_choice,
        "reason": "SMALLEST_POSITIVE_PARETO_SAFE" if passed_weights else "NO_POSITIVE_WEIGHT_PASSED_KEEP_ALS",
        "baseline": warm_baseline,
        "candidates": warm_metrics,
    }
    assert_nested_equal(expected_warm, selection["warm"], "warm selection")

    cold = frame[frame.selection.eq("COLD_HEAD")]
    require(cold.weight.isna().all(), "cold rows have no hybrid weight")
    cold_names = [*config["selection"]["cold"]["candidates"], config["selection"]["cold"]["incumbent"]]
    require(set(cold.candidate.astype(str)) == set(cold_names), "cold candidate inventory")
    cold_eligible: set[int] | None = None
    for name in cold_names:
        eligible = set(cold.loc[cold.candidate.eq(name) & cold.mse.notna() & cold.ndcg2.notna(), "uid"].astype(int))
        cold_eligible = eligible if cold_eligible is None else cold_eligible & eligible
    cold_uids = sorted(cold_eligible or set())
    require(np.array_equal(cold.common_selection_uid.to_numpy(bool), cold.uid.isin(cold_uids).to_numpy()),
            "cold common UID marker")
    cold_metrics = {name: selection_candidate_summary(cold[cold.candidate.eq(name) & cold.common_selection_uid])
                    for name in sorted(cold_names)}
    incumbent = str(config["selection"]["cold"]["incumbent"])
    passed = [name for name in config["selection"]["cold"]["candidates"]
              if pareto_safe(cold_metrics[name], cold_metrics[incumbent], safety)]
    order = {name: index for index, name in enumerate(config["selection"]["cold"]["candidates"])}
    passed.sort(key=lambda name: (-float(cold_metrics[name]["ndcg2"]),
                                  float(cold_metrics[name]["mse"]), order[name]))
    cold_choice = passed[0] if passed else incumbent
    expected_cold = {"head": cold_choice,
                     "reason": "BEST_PARETO_SAFE_CHALLENGER" if passed else "NO_CHALLENGER_PASSED_KEEP_GBT120_s339",
                     "incumbent": incumbent, "eligible_challengers": passed, "candidates": cold_metrics}
    assert_nested_equal(expected_cold, selection["cold"], "cold selection")
    require(selection.get("roles_used") == ["calibration"] and
            selection.get("comparison_labels_used") is False and
            selection.get("service_adoption") is False and selection.get("scope") == "DEVELOPMENT_ONLY" and
            int(selection.get("cap", -1)) == int(config["primary_cap"]) and
            selection.get("common_denominator_policy") ==
            "ALL_DECLARED_MODELS_SCORE_EVERY_TARGET_OF_THE_USER_GROUP", "selection scope/leakage statement")
    return {"warm_common_users": len(warm_uids), "cold_common_users": len(cold_uids),
            "warm_weight": warm_choice, "cold_head": cold_choice}


def derive_contrast_directions(contrasts: pd.DataFrame) -> pd.DataFrame:
    result = contrasts.copy()
    directions = []
    for row in result.itertuples():
        if pd.isna(row.ci_low):
            directions.append("DESCRIPTIVE_SMALL_N")
        elif (row.better == "lower" and row.ci_high < 0) or (row.better == "higher" and row.ci_low > 0):
            directions.append("AFTER_BETTER")
        elif (row.better == "lower" and row.ci_low > 0) or (row.better == "higher" and row.ci_high < 0):
            directions.append("AFTER_WORSE")
        else:
            directions.append("NO_CLEAR_DIFFERENCE")
    result["direction"] = directions
    return result


def recompute_decisions(contrasts: pd.DataFrame, users: pd.DataFrame,
                        selection: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    contrasts = derive_contrast_directions(contrasts)
    minimum = int(config["bootstrap"]["minimum_users"])
    decisions = []
    keys = contrasts[["family", "before", "after", "group"]].drop_duplicates()
    for family, before, after, group in keys.itertuples(index=False, name=None):
        rows = contrasts[contrasts.family.eq(family) & contrasts.before.eq(before) &
                         contrasts.after.eq(after) & contrasts.group.eq(group)]
        safety_before = "ALS" if family == "warm" else "GBT120_s339"
        subset = users[users.cap.eq(10) & users.h.gt(0) & users.group.eq(group)]
        pivots = {metric: subset.pivot(index="uid", columns="model", values=metric).sort_index()
                  for metric in ("stars2", "low2")}
        common = sorted(set(pivots["stars2"][[safety_before, after]].dropna().index) &
                        set(pivots["low2"][[safety_before, after]].dropna().index))
        star_delta = float((pivots["stars2"].loc[common, after] -
                            pivots["stars2"].loc[common, safety_before]).mean()) if common else None
        low_delta = float((pivots["low2"].loc[common, after] -
                           pivots["low2"].loc[common, safety_before]).mean()) if common else None
        safe = (star_delta is not None and low_delta is not None and
                star_delta >= -float(config["safety"]["max_top2_stars_loss"]) - ATOL and
                low_delta <= float(config["safety"]["max_low2_increase"]) + ATOL)
        directions = set(rows.direction)
        small = bool(rows.users.lt(minimum).any() or len(common) < minimum)
        favorable, harmful = "AFTER_BETTER" in directions, "AFTER_WORSE" in directions
        verdict = ("DESCRIPTIVE_SMALL_N" if small else "TRADEOFF" if favorable and harmful else
                   "DETECTED_HARM" if (not safe or harmful) else
                   "ONE_METRIC_ADVANTAGE_NO_DETECTED_HARM" if favorable else "NO_CLEAR_DIFFERENCE")
        decisions.append({"family": family, "before": before, "after": after, "group": group,
                          "confidence": float(rows.confidence.iloc[0]),
                          "mse_users": int(rows.loc[rows.metric.eq("mse"), "users"].iloc[0]),
                          "ndcg2_users": int(rows.loc[rows.metric.eq("ndcg2"), "users"].iloc[0]),
                          "verdict": verdict, "safety_pass": bool(safe), "safety_before": safety_before,
                          "safety_users": len(common), "stars2_delta": star_delta, "low2_delta": low_delta})
    return {"scope": "DEVELOPMENT_ONLY", "comparison_role_only": True,
            "bootstrap_uncertainty": "USER_SAMPLING_CONDITIONAL_ON_FIXED_MODELS_MAPPER_AND_CALIBRATORS",
            "shared_bootstrap_within_family": False,
            "seed_derivation": config["bootstrap"]["seed_derivation"],
            "not_equivalence_or_noninferiority_testing": True, "pair_decisions": decisions,
            "allowed_positive_label": "ONE_METRIC_ADVANTAGE_NO_DETECTED_HARM", "service_adoption": False,
            "selection": {"warm_content_weight": selection["warm"]["content_weight"],
                          "cold_head": selection["cold"]["head"]},
            "natural_zero_inference": "DESCRIPTIVE_ONLY_IF_NDCG2_VALID_USERS_BELOW_30"}


MODEL_SCOPES = {
    "QWEN_DIRECT": "FULL_EI_WHERE_MODEL_AVAILABLE",
    "ALS_C2F": "FULL_EI_WHERE_MODEL_AVAILABLE",
    "ALS_QWEN": "ALS_WARM_AVAILABLE_WITHIN_EI",
    "SELECTED_COLD_HEAD": "FULL_EI_WHERE_SELECTED_HEAD_AVAILABLE",
    "ROUTER_s339": "FULL_EI_WITH_DECLARED_GBT_FALLBACK",
}


def validate_catalog_top(top: pd.DataFrame, supply: pd.DataFrame, labels: pd.DataFrame,
                         config: Mapping[str, Any], catalog_seal: Mapping[str, Any],
                         selection: Mapping[str, Any]) -> tuple[int, int]:
    catalog = pd.read_parquet(ROOT / config["sources"]["catalog"])
    partition = pd.read_parquet(ROOT / config["sources"]["partition"])
    require(np.array_equal(catalog.movie_id.to_numpy(), partition.movie_id.to_numpy()), "catalog partition axis")
    require(np.array_equal(catalog.blocked.to_numpy(bool),
                           partition.partition.astype(str).isin(["V", "E"]).to_numpy()),
            "catalog blocked/partition contract")
    ids = catalog.movie_id.to_numpy(np.int64)
    texts = pd.read_parquet(ROOT / config["sources"]["texts"], columns=["movie_id", "release_date"])
    require(np.array_equal(texts.movie_id.to_numpy(np.int64), ids), "catalog release-date axis")
    dates = pd.to_datetime(texts.release_date, format="%Y-%m-%d", errors="coerce", utc=True)
    released = dates.notna().to_numpy() & (dates.astype("int64").to_numpy() // 10**9 <=
                                           int(config["catalog_snapshot_timestamp"]))
    contexts = read_json(ROOT / config["sources"]["contexts"])
    roles = pd.read_csv(ROOT / config["sources"]["roles"])
    comparison = set(roles.loc[roles.role.eq("comparison"), "uid"].astype(int))
    selected_contexts = {int(c["uid"]): c for c in contexts
                         if int(c["cap"]) == 10 and int(c["h"]) > 0 and int(c["uid"]) in comparison}
    expected_uids = set(selected_contexts)
    require(len(expected_uids) == int(catalog_seal["users"]), "catalog selected user denominator")
    require(set(supply.uid.astype(int)) == expected_uids and
            set(supply.model.astype(str)) == set(CATALOG_MODELS) and
            len(supply) == len(expected_uids) * len(CATALOG_MODELS) and
            not supply.duplicated(["uid", "model"]).any(), "catalog supply user/model grid")
    require(set(top.uid.astype(int)).issubset(expected_uids) and set(top.model) == set(CATALOG_MODELS),
            "catalog top user/model identity")
    require(not top.duplicated(["uid", "model", "rank"]).any() and
            not top.duplicated(["uid", "model", "movie_id"]).any(), "catalog top rank/movie uniqueness")

    final_cache = ROOT / config["sources"]["final344_catalog_cache"]
    eligible_union: set[int] = set()
    label_index = pd.MultiIndex.from_frame(labels[["uid", "movie_id"]])
    catalog_index = {int(movie): index for index, movie in enumerate(ids)}
    for uid, context in selected_contexts.items():
        cache = np.load(final_cache / f"{uid}.npz", allow_pickle=False)
        candidates = np.asarray(cache["ei"], np.int64)
        allowed = released.copy()
        viewed = np.asarray(context["viewed"], np.int64)
        require(len(np.unique(viewed)) == len(viewed) and ((viewed >= 0) & (viewed < len(ids))).all(),
                f"catalog legal viewed axis:{uid}")
        allowed[viewed] = False
        require(np.array_equal(candidates, np.flatnonzero(allowed)), f"catalog candidate-axis contract:{uid}")
        candidate_movies = set(map(int, ids[candidates]))
        eligible_union.update(map(int, candidates))
        for model in CATALOG_MODELS:
            supply_row = supply[supply.uid.eq(uid) & supply.model.eq(model)].iloc[0]
            require(int(supply_row.candidates) == len(candidates) and
                    int(supply_row.finite_scores) + int(supply_row.unavailable_scores) == len(candidates),
                    f"catalog supply counts:{uid}/{model}")
            current = top[top.uid.eq(uid) & top.model.eq(model)].sort_values("rank")
            expected_count = min(6, int(supply_row.finite_scores))
            require(len(current) == expected_count and
                    np.array_equal(current["rank"].to_numpy(int), np.arange(1, expected_count + 1)),
                    f"catalog top completeness:{uid}/{model}")
            require(set(current.movie_id.astype(int)).issubset(candidate_movies), f"catalog candidate membership:{uid}/{model}")
    ix = top.movie_id.map(catalog_index)
    require(ix.notna().all() and np.array_equal(ix.to_numpy(int), top.catalog_index.to_numpy(int)),
            "catalog top movie/index identity")
    positions = ix.to_numpy(int)
    support = catalog.train_count.to_numpy(np.int64)[positions]
    blocked = catalog.blocked.to_numpy(bool)[positions]
    actual_support = (~blocked) & (support > 0)
    require(np.array_equal(top.support.to_numpy(np.int64), support) and
            np.array_equal(top.blocked.to_numpy(bool), blocked) and
            np.array_equal(top.actual_als_supported.to_numpy(bool), actual_support),
            "catalog top support metadata")
    observed = pd.MultiIndex.from_frame(top[["uid", "movie_id"]]).isin(label_index)
    require(np.array_equal(top.unknown.to_numpy(bool), ~observed), "catalog unknown label re-derivation")
    require(np.array_equal(top.candidate_scope.astype(str), top.model.map(MODEL_SCOPES).astype(str)),
            "catalog candidate scope")
    require(np.allclose(top.selected_warm_weight.to_numpy(float), float(selection["warm"]["content_weight"])) and
            top.selected_cold_head.astype(str).eq(str(selection["cold"]["head"])).all(), "catalog selection columns")
    require(len(eligible_union) == int(catalog_seal["eligible_catalog_movies"]),
            "catalog eligible-union denominator")
    return len(expected_uids), len(eligible_union)


def bootstrap_seed(configured_seed: int, family: str, after: str, before: str, metric: str) -> int:
    body = f"hybrid345-bootstrap-v1|{configured_seed}|{family}|{after}|{before}|{metric}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(body).digest()[:8], "big", signed=False)


def bootstrap_interval(delta: np.ndarray, samples: int, seed: int, confidence: float,
                       minimum_users: int) -> tuple[float | None, float | None]:
    delta = np.asarray(delta, dtype=np.float64)
    if len(delta) < minimum_users:
        return None, None
    rng = np.random.Generator(np.random.PCG64(seed))
    means = np.empty(samples, dtype=np.float64)
    for start in range(0, samples, 256):
        stop = min(samples, start + 256)
        draw = rng.integers(0, len(delta), size=(stop - start, len(delta)))
        means[start:stop] = delta[draw].mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    low, high = np.quantile(means, [alpha, 1.0 - alpha])
    return float(low), float(high)


def recompute_contrasts(users: pd.DataFrame, config: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    settings = config["bootstrap"]
    rows, pairs = [], []
    for spec in config["formal_contrasts"]:
        subset = users[users.cap.eq(int(spec["cap"])) & users.h.gt(0) & users.group.eq(spec["group"])]
        pivot = subset.pivot(index="uid", columns="model", values=spec["metric"]).sort_index()
        require(spec["before"] in pivot and spec["after"] in pivot, "contrast model columns")
        pair = pivot[[spec["before"], spec["after"]]].dropna()
        uids = pair.index.to_numpy(np.int64)
        require(np.array_equal(uids, np.sort(np.unique(uids))), "contrast UID order")
        delta = pair[spec["after"]].to_numpy(float) - pair[spec["before"]].to_numpy(float)
        seed = bootstrap_seed(int(settings["seed"]), spec["family"], spec["after"], spec["before"], spec["metric"])
        low, high = bootstrap_interval(delta, int(settings["samples"]), seed,
                                       float(spec["confidence"]), int(settings["minimum_users"]))
        rows.append({**spec, "users": len(pair),
                     "configured_seed": int(settings["seed"]),
                     "before_mean": float(pair[spec["before"]].mean()) if len(pair) else None,
                     "after_mean": float(pair[spec["after"]].mean()) if len(pair) else None,
                     "delta": float(delta.mean()) if len(delta) else None,
                     "ci_low": low, "ci_high": high, "confidence": float(spec["confidence"]),
                     "samples": int(settings["samples"]), "seed": seed,
                     "bootstrap_stream": "PCG64_INDEPENDENT_SHA256_DERIVED_SEED_ASCENDING_COMMON_FINITE_UID"})
        for uid, before, after in zip(uids, pair[spec["before"]], pair[spec["after"]]):
            pairs.append({"family": spec["family"], "before": spec["before"], "after": spec["after"],
                          "group": spec["group"], "metric": spec["metric"], "uid": int(uid),
                          "before_value": float(before), "after_value": float(after),
                          "delta": float(after - before)})
    require(len(rows) == 14, "exact 14 formal contrasts")
    return derive_contrast_directions(pd.DataFrame(rows)), pd.DataFrame(pairs)


def compare_contrasts(actual: pd.DataFrame, published: pd.DataFrame,
                      actual_pairs: pd.DataFrame, published_pairs: pd.DataFrame) -> None:
    keys = ["family", "before", "after", "group", "metric"]
    require(len(published) == 14 and not published.duplicated(keys).any(), "published exact 14 contrasts")
    merged = actual.merge(published, on=keys, how="outer", suffixes=("_audit", "_published"), indicator=True)
    require(merged._merge.eq("both").all(), "contrast key mismatch")
    for name in ("users", "samples", "seed"):
        require((merged[name + "_audit"].astype(np.uint64) == merged[name + "_published"].astype(np.uint64)).all(),
                f"contrast {name} mismatch")
    require((merged.configured_seed_audit.astype(int) == 346).all() and
            (merged.configured_seed_published.astype(int) == 346).all(), "contrast configured seed")
    for name in ("before_mean", "after_mean", "delta", "ci_low", "ci_high", "confidence"):
        require(np.allclose(merged[name + "_audit"].to_numpy(float),
                            merged[name + "_published"].to_numpy(float),
                            rtol=0.0, atol=ATOL, equal_nan=True), f"contrast {name} mismatch")
    expected_direction = derive_contrast_directions(actual)[keys + ["direction"]]
    direction_join = expected_direction.merge(published[keys + ["direction"]], on=keys,
                                               suffixes=("_audit", "_published"))
    require((direction_join.direction_audit == direction_join.direction_published).all(),
            "contrast direction mismatch")
    pair_keys = [*keys, "uid"]
    require(not published_pairs.duplicated(pair_keys).any(), "published paired UID duplicates")
    joined = actual_pairs.merge(published_pairs, on=pair_keys, how="outer",
                                suffixes=("_audit", "_published"), indicator=True)
    require(joined._merge.eq("both").all(), "paired UID membership mismatch")
    for name in ("before_value", "after_value", "delta"):
        require(np.allclose(joined[name + "_audit"], joined[name + "_published"],
                            rtol=0.0, atol=ATOL), f"paired values mismatch: {name}")


def recompute_catalog(top: pd.DataFrame, users: int, eligible_movies: int) -> pd.DataFrame:
    required = {"uid", "model", "rank", "movie_id", "unknown", "support", "actual_als_supported", "blocked"}
    require(required.issubset(top.columns), "catalog top6 schema")
    rows = []
    for model in CATALOG_MODELS:
        model_rows = top[top.model.eq(model)]
        require(model_rows.uid.nunique() <= users, f"catalog users: {model}")
        require(not model_rows.duplicated(["uid", "rank"]).any(), f"catalog rank uniqueness: {model}")
        require(model_rows["rank"].between(1, 6).all(), f"catalog rank range: {model}")
        for n in TOP_NS:
            sample = model_rows[model_rows["rank"] <= n]
            counts = sample.movie_id.value_counts()
            returned, slots = len(sample), users * n
            shares = counts.to_numpy(float) / returned if returned else np.empty(0)
            rows.append({
                "model": model, "top_n": n, "users": users, "slots": slots,
                "returned": returned, "missing": slots - returned,
                "unique_movies": len(counts), "catalog_coverage": len(counts) / eligible_movies,
                "unknown": int(sample.unknown.sum()),
                "unknown_rate": float(sample.unknown.mean()) if returned else math.nan,
                "hhi": float(np.square(shares).sum()) if returned else math.nan,
                "max_share": float(shares.max()) if returned else math.nan,
                "support0": int(sample.support.eq(0).sum()),
                "support1_9": int(sample.support.between(1, 9).sum()),
                "support10_49": int(sample.support.between(10, 49).sum()),
                "support50plus": int(sample.support.ge(50).sum()),
                "actual_als_supported": int(sample.actual_als_supported.sum()),
                "blocked": int(sample.blocked.sum()),
            })
    return pd.DataFrame(rows)


def compare_catalog(actual: pd.DataFrame, published: pd.DataFrame) -> None:
    keys = ["model", "top_n"]
    require(len(published) == len(CATALOG_MODELS) * len(TOP_NS), "catalog summary row count")
    merged = actual.merge(published, on=keys, how="outer", suffixes=("_audit", "_published"), indicator=True)
    require(merged._merge.eq("both").all(), "catalog summary key mismatch")
    integers = ("users", "slots", "returned", "missing", "unique_movies", "unknown",
                "support0", "support1_9", "support10_49", "support50plus",
                "actual_als_supported", "blocked")
    for name in integers:
        require((merged[name + "_audit"].astype(int) == merged[name + "_published"].astype(int)).all(),
                f"catalog integer mismatch: {name}")
    for name in ("catalog_coverage", "unknown_rate", "hhi", "max_share"):
        require(np.allclose(merged[name + "_audit"], merged[name + "_published"],
                            rtol=0.0, atol=ATOL, equal_nan=True), f"catalog value mismatch: {name}")


def formatted(value: Any, digits: int = 4) -> str:
    return "N/A" if value is None or pd.isna(value) else f"{float(value):.{digits}f}"


def summary_value(summary: pd.DataFrame, model: str, group: str, metric: str) -> tuple[float, int]:
    row = summary[summary.model.eq(model) & summary.group.eq(group) & summary.cap.eq(10)
                  & summary.h_group.eq("H_POSITIVE") & summary.metric.eq(metric)]
    require(len(row) == 1, f"report summary value: {model}/{group}/{metric}")
    return float(row.iloc[0]["mean"]), int(row.iloc[0]["valid_users"])


def markdown_section_rows(text: str, heading: str) -> dict[str, list[list[str]]]:
    require(heading in text, f"missing RESULT section: {heading}")
    body = text.split(heading, 1)[1]
    if "\n## " in body:
        body = body.split("\n## ", 1)[0]
    rows: dict[str, list[list[str]]] = {}
    for line in body.splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] not in {"모델", "family"}:
            rows.setdefault(cells[0], []).append(cells)
    return rows


def verify_report_text(text: str, summary: pd.DataFrame, catalog: pd.DataFrame,
                       selection: Mapping[str, Any], contrasts: pd.DataFrame,
                       page_summary: pd.DataFrame, denominators: pd.DataFrame,
                       diagnostics: pd.DataFrame, decisions: Mapping[str, Any]) -> None:
    require("상태: **DEVELOPMENT_ONLY**" in text, "RESULT development-only status")
    require("2026년 한국 서비스 만족도나 배포 정책을 직접 입증하지 않는다." in text,
            "RESULT service-scope limitation")
    require("동등성·비열등성이나 서비스 우위를 입증하지 않는다." in text,
            "RESULT statistical-scope limitation")
    require(f"- warm 결합 가중치: **{selection['warm']['content_weight']}**" in text,
            "RESULT warm selection")
    require(f"- cold head: **{selection['cold']['head']}**" in text, "RESULT cold selection")
    require(f"(`{selection['warm']['reason']}`)" in text and f"(`{selection['cold']['reason']}`)" in text,
            "RESULT selection reasons")

    sections = (
        ("## ALS가 직접 계산되는 W_DIRECT", "W_DIRECT", ("ALS", "QWEN_DIRECT", "ALS_QWEN")),
        ("## ALS가 직접 계산되지 않는 C", "C", ("STRUCTURED_DIRECT", "E5_DIRECT", "QWEN_DIRECT",
                                                      "FM150_SEED_MEAN", "GBT120_SEED_MEAN", "ALS_C2F")),
        ("## 전체 관측 후보와 support-aware router", "ALL",
         ("FM150_SEED_MEAN", "GBT120_SEED_MEAN", "ROUTER_s339")),
    )
    for heading, group, models in sections:
        rows = markdown_section_rows(text, heading)
        for model in models:
            require(model in rows and len(rows[model]) == 1, f"RESULT model row: {heading}/{model}")
            cells = rows[model][0]
            require(cells[1] == formatted(summary_value(summary, model, group, "mse")[0]),
                    f"RESULT MSE: {model}/{group}")
            require(cells[2] == formatted(summary_value(summary, model, group, "mae")[0]) and
                    cells[3] == formatted(summary_value(summary, model, group, "bias")[0]) and
                    cells[4] == formatted(summary_value(summary, model, group, "pa")[0]),
                    f"RESULT error/PA metrics: {model}/{group}")
            expected_ndcg = "/".join(formatted(summary_value(summary, model, group, f"ndcg{n}")[0])
                                      for n in (1, 2, 4, 6))
            expected_stars = "/".join(formatted(summary_value(summary, model, group, f"stars{n}")[0])
                                       for n in (1, 2, 4, 6))
            expected_good = "/".join(formatted(summary_value(summary, model, group, f"good{n}")[0])
                                      for n in (1, 2, 4, 6))
            expected_low = "/".join(formatted(summary_value(summary, model, group, f"low{n}")[0])
                                     for n in (1, 2, 4, 6))
            require(cells[5] == expected_ndcg and cells[6] == expected_stars and cells[7] == expected_good and
                    cells[8] == expected_low and
                    cells[9] == formatted(summary_value(summary, model, group, "any_low2")[0]) and
                    cells[10] == formatted(summary_value(summary, model, group, "both_low2")[0]),
                    f"RESULT top-N metrics: {model}/{group}")
            mse_users = summary_value(summary, model, group, "mse")[1]
            ndcg_users = "/".join(str(summary_value(summary, model, group, f"ndcg{n}")[1])
                                  for n in (1, 2, 4, 6))
            require(cells[11] == f"{mse_users} / {ndcg_users}", f"RESULT metric denominators: {model}/{group}")

    catalog_rows = markdown_section_rows(text, "## 전체 카탈로그 Top2/4/6 진단")
    for row in catalog.itertuples(index=False):
        matches = [cells for cells in catalog_rows.get(row.model, []) if cells[1] == str(int(row.top_n))]
        require(len(matches) == 1, f"RESULT catalog row: {row.model}/top{row.top_n}")
        cells = matches[0]
        require(cells[3] == f"{int(row.returned)}/{int(row.slots)}" and
                cells[4] == str(int(row.unique_movies)) and
                cells[5] == formatted(row.catalog_coverage, 6) and
                cells[6] == f"{int(row.unknown)} ({100 * float(row.unknown_rate):.2f}%)" and
                cells[7] == formatted(row.hhi, 6) and cells[8] == formatted(row.max_share, 6) and
                cells[9] == str(int(row.support0)), f"RESULT catalog values: {row.model}/top{row.top_n}")

    contrast_rows = markdown_section_rows(text, "## 사전 고정 14개 대조")
    require(sum(len(value) for value in contrast_rows.values()) == 14, "RESULT exact 14 contrast rows")
    for row in contrasts.itertuples(index=False):
        comparison = f"{row.after} − {row.before}"
        candidates = [cells for cells in contrast_rows.get(row.family, [])
                      if cells[1] == comparison and cells[2] == row.group and cells[3] == row.metric]
        require(len(candidates) == 1, f"RESULT contrast row: {comparison}/{row.metric}")
        interval = "N/A" if pd.isna(row.ci_low) else f"[{row.ci_low:.6g}, {row.ci_high:.6g}]"
        cells = candidates[0]
        require(cells[4] == str(int(row.users)) and cells[5] == formatted(row.delta, 6)
                and cells[6] == interval, f"RESULT contrast values: {comparison}/{row.metric}")

    page_rows = markdown_section_rows(text, "## 2편씩 이어지는 페이지")
    for model in ("FM150_SEED_MEAN", "GBT120_SEED_MEAN", "ROUTER_s339"):
        for end in (2, 4, 6):
            current = page_summary[page_summary.model.eq(model) & page_summary.cap.eq(10) &
                                   page_summary.group.eq("ALL") & page_summary.h_group.eq("H_POSITIVE") &
                                   page_summary.start.eq(end - 2) & page_summary.end.eq(end)]
            values = {metric: current[current.metric.eq(metric)].iloc[0]
                      for metric in ("stars", "good", "low", "any_low", "both_low")}
            matches = [cells for cells in page_rows.get(model, []) if cells[1] == f"{end-1}–{end}"]
            require(len(matches) == 1, f"RESULT page row:{model}/{end}")
            cells = matches[0]
            require(cells[2] == str(int(values["stars"].valid_users)) and
                    cells[3] == formatted(values["stars"]["mean"]) and
                    cells[4] == formatted(values["good"]["mean"]) and
                    cells[5] == formatted(values["low"]["mean"]) and
                    cells[6] == formatted(values["any_low"]["mean"]) and
                    cells[7] == formatted(values["both_low"]["mean"]), f"RESULT page values:{model}/{end}")

    denominator_rows = markdown_section_rows(text, "## 분모")
    warm_models = ["ALS", "QWEN_DIRECT", "ALS_QWEN"]
    cold_models = ["STRUCTURED_DIRECT", "E5_DIRECT", "QWEN_DIRECT", "FM150_SEED_MEAN", "GBT120_SEED_MEAN", "ALS_C2F"]
    router_models = ["FM150_SEED_MEAN", "GBT120_SEED_MEAN", "ROUTER_s339"]
    expected_denoms = denominators[denominators.cap.eq(10) & denominators.h_group.eq("H_POSITIVE") &
                                     denominators.model.isin(set(warm_models + cold_models + router_models)) &
                                     denominators.group.isin(["W_DIRECT", "C", "ALL"])]
    require(sum(len(rows) for rows in denominator_rows.values()) == len(expected_denoms),
            "RESULT denominator row count")
    for row in expected_denoms.itertuples(index=False):
        matches = [cells for cells in denominator_rows.get(row.model, []) if cells[1] == row.group]
        require(len(matches) == 1, f"RESULT denominator row:{row.model}/{row.group}")
        require(matches[0][2:] == [str(int(row.user_contexts)), str(int(row.complete_user_contexts)),
                                   str(int(row.target_rows)), str(int(row.scored_rows))],
                f"RESULT denominator values:{row.model}/{row.group}")

    diagnostic_rows = markdown_section_rows(text, "## V/E 방향 진단")
    for model in ("QWEN_DIRECT", "ALS_C2F", "FM150_s339", "GBT120_s339"):
        for level in ("V", "E"):
            row = diagnostics[diagnostics.model.eq(model) & diagnostics.dimension.eq("cold_partition") &
                              diagnostics.level.eq(level)]
            require(len(row) == 1, f"diagnostic source row:{model}/{level}")
            row = row.iloc[0]
            matches = [cells for cells in diagnostic_rows.get(model, []) if cells[1] == level]
            require(len(matches) == 1 and matches[0][2:] == [str(int(row.users)), str(int(row["rows"])),
                    str(int(row.movies)), formatted(row.user_macro_mse)],
                    f"RESULT diagnostic values:{model}/{level}")

    verdict_counts = pd.Series([row["verdict"] for row in decisions["pair_decisions"]]).value_counts().to_dict()
    require(f"판정 집계: `{json.dumps(verdict_counts, ensure_ascii=False, sort_keys=True)}`" in text,
            "RESULT verdict counts")


def audit(output_dir: Path = OUT, result_path: Path = RESULT,
          audit_output: Path = AUDIT_OUTPUT, *, overwrite: bool = False) -> dict[str, Any]:
    output_dir, result_path, audit_output = map(Path, (output_dir, result_path, audit_output))
    require(overwrite or not audit_output.exists(), f"preserve existing result audit: {audit_output}")

    # Critical ordering: no labels or label-derived tables are opened before this returns.
    gates = verify_all_gates(output_dir, result_path)
    config = gates["config"]

    label_path = ROOT / config["sources"]["labels"]
    label_pin = gates["evaluation_manifest"]["inputs"]["labels"]
    assert_pin(label_path, label_pin, "frozen evaluation labels")
    labels = pd.read_parquet(label_path, columns=["uid", "movie_id", "rating"])
    require(np.isfinite(labels.rating).all() and
            np.isin(labels.rating.to_numpy(float), np.arange(1, 11) / 2).all(), "half-star label values")
    require(not labels.duplicated(["uid", "movie_id"]).any(), "unique user/movie labels")

    errors = pd.read_parquet(output_dir / "row-errors.parquet")
    validate_and_enrich_row_errors(errors, labels, config)
    recomputed_users, recomputed_pages = recompute_from_row_errors(errors)
    users = pd.read_parquet(output_dir / "user-metrics.parquet")
    pages = pd.read_parquet(output_dir / "page-metrics.parquet")
    compare_metric_table(recomputed_users, users)
    compare_metric_table(recomputed_pages, pages, page=True)

    summary = pd.read_csv(output_dir / "summary.csv")
    denominators = pd.read_csv(output_dir / "denominators.csv")
    recomputed_summary = recompute_core_summary(recomputed_users)
    compare_core_summary(recomputed_summary, summary)
    compare_denominators(recomputed_users, denominators)
    page_summary = pd.read_csv(output_dir / "page-summary.csv")
    recomputed_page_summary = summarize_pages_independent(recomputed_pages)
    compare_summary_like(recomputed_page_summary, page_summary,
                         ["model", "cap", "group", "h_group", "start", "end", "metric"])
    diagnostics = pd.read_csv(output_dir / "diagnostic-summary.csv")
    recomputed_diagnostic = recompute_diagnostics(errors)
    compare_diagnostics(recomputed_diagnostic, diagnostics)

    selection = read_json(output_dir / "selection.json")
    selection_metrics = pd.read_parquet(output_dir / "selection-user-metrics.parquet")
    selection_check = verify_selection_metrics(selection_metrics, selection, config)

    recomputed_contrasts, recomputed_pairs = recompute_contrasts(recomputed_users, config)
    published_contrasts = pd.read_csv(output_dir / "primary-contrasts.csv")
    published_pairs = pd.read_parquet(output_dir / "primary-paired-users.parquet")
    compare_contrasts(recomputed_contrasts, published_contrasts, recomputed_pairs, published_pairs)
    published_decisions = read_json(output_dir / "decisions.json")
    recomputed_decisions = recompute_decisions(recomputed_contrasts, recomputed_users, selection, config)
    assert_nested_equal(recomputed_decisions, published_decisions, "decisions")

    top = pd.read_parquet(output_dir / "catalog-top6.parquet")
    supply = pd.read_csv(output_dir / "catalog-supply.csv")
    catalog_users, eligible_movies = validate_catalog_top(
        top, supply, labels, config, gates["catalog_seal"], selection
    )
    timing = pd.read_csv(output_dir / "catalog-timing.csv")
    expected_catalog_uids = set(supply.uid.astype(int))
    require(len(timing) == catalog_users and not timing.uid.duplicated().any() and
            set(timing.uid.astype(int)) == expected_catalog_uids and
            np.isfinite(timing.seconds).all() and timing.seconds.ge(0).all(), "catalog timing user grid")
    candidate_counts = supply.groupby("uid", sort=True).candidates.nunique()
    require(candidate_counts.eq(1).all(), "catalog candidate count shared by all models")
    expected_candidates = supply.groupby("uid", sort=True).candidates.first()
    timing_candidates = timing.set_index("uid").candidates
    require((expected_candidates.astype(int) == timing_candidates.loc[expected_candidates.index].astype(int)).all(),
            "catalog timing/supply candidate counts")
    published_catalog = pd.read_csv(output_dir / "catalog-summary.csv")
    recomputed_catalog_summary = recompute_catalog(
        top, catalog_users, eligible_movies
    )
    compare_catalog(recomputed_catalog_summary, published_catalog)
    require(published_catalog.candidate_scope.astype(str).eq(
            published_catalog.model.map(MODEL_SCOPES).astype(str)).all() and
            published_catalog.eligible_catalog_movies.astype(int).eq(eligible_movies).all() and
            np.allclose(published_catalog.selected_warm_weight.to_numpy(float),
                        float(selection["warm"]["content_weight"])) and
            published_catalog.selected_cold_head.astype(str).eq(str(selection["cold"]["head"])).all(),
            "catalog summary contract fields")
    require(gates["catalog_seal"].get("models") == list(CATALOG_MODELS) and
            gates["catalog_seal"].get("model_candidate_scopes") == MODEL_SCOPES and
            gates["catalog_seal"].get("top_ns") == list(TOP_NS) and
            gates["catalog_seal"].get("unknown_definition") == "not_in_observed_user_movie_labels" and
            float(gates["catalog_seal"].get("selected_warm_weight")) == float(selection["warm"]["content_weight"]) and
            gates["catalog_seal"].get("selected_cold_head") == selection["cold"]["head"],
            "catalog seal metric/selection contract")

    require(gates["report_manifest"]["selection"] == {
        "warm_content_weight": selection["warm"]["content_weight"],
        "cold_head": selection["cold"]["head"],
    }, "report manifest selection drift")
    result_text = result_path.read_text(encoding="utf-8")
    verify_report_text(result_text, summary, published_catalog, selection, published_contrasts,
                       page_summary, denominators, diagnostics, published_decisions)
    assert_pin(label_path, label_pin, "frozen evaluation labels after audit")
    # Recheck every result file opened by the audit to close the read/write race.
    evaluation_seal = read_json(output_dir / "evaluation-seal.json")
    verify_pin_map(output_dir, evaluation_seal["files"], "evaluation output after audit")
    catalog_seal = read_json(output_dir / "catalog-diagnostic-seal.json")
    verify_pin_map(output_dir, catalog_seal["files"], "catalog output after audit")
    assert_pin(result_path, gates["report_manifest"]["result"], "RESULT.md after audit")
    require(pin(CONFIG) == gates["config_pin"] and read_json(CONFIG) == config, "config changed during audit")
    final_reviews = {
        "prelabel": verify_review(PRELABEL_REVIEW, PRELABEL_FILES, "prelabel")["fingerprint"],
        "evaluation": verify_review(EVALUATION_REVIEW, EVALUATION_FILES, "evaluation")["fingerprint"],
        "catalog": verify_review(CATALOG_REVIEW, CATALOG_FILES, "catalog")["fingerprint"],
        "audit": verify_review(AUDIT_REVIEW, AUDIT_FILES, "result audit")["fingerprint"],
    }
    require(final_reviews == gates["review_fingerprints"], "review/code fingerprint changed during audit")
    final_gates = verify_all_gates(output_dir, result_path)
    require(final_gates["config_pin"] == gates["config_pin"] and
            final_gates["review_fingerprints"] == gates["review_fingerprints"] and
            final_gates["reviews"] == gates["reviews"] and
            final_gates["seals"] == gates["seals"] and
            final_gates["report_manifest"] == gates["report_manifest"],
            "recursive lineage changed during audit")

    result = {
        "schema_version": 1,
        "status": "PASS",
        "scope": "DEVELOPMENT_ONLY",
        "service_adoption": False,
        "labels_opened_after_all_lineage_gates": True,
        "labels": {"pin": pin(label_path), "rows": int(len(labels)),
                   "users": int(labels.uid.nunique()), "movies": int(labels.movie_id.nunique())},
        "reviews": gates["reviews"],
        "seals": gates["seals"],
        "auditor_fingerprint": fingerprint(AUDIT_FILES),
        "checks": {
            "summary_core_metrics": int(len(recomputed_summary)),
            "row_error_rows": int(len(errors)),
            "user_metric_rows": int(len(recomputed_users)),
            "page_metric_rows": int(len(recomputed_pages)),
            "page_summary_rows": int(len(recomputed_page_summary)),
            "diagnostic_rows": int(len(recomputed_diagnostic)),
            "selection": selection_check,
            "denominator_groups": int(len(denominators)),
            "formal_contrasts": int(len(recomputed_contrasts)),
            "paired_contrast_rows": int(len(recomputed_pairs)),
            "bootstrap_samples_per_eligible_contrast": int(config["bootstrap"]["samples"]),
            "catalog_rows": int(len(recomputed_catalog_summary)),
            "catalog_users": catalog_users,
            "catalog_eligible_movies": eligible_movies,
            "result_report": "MATCH",
            "development_only_language": "PASS",
        },
    }
    write_json_atomic(audit_output, result)
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--result", type=Path, default=RESULT)
    parser.add_argument("--audit-output", type=Path, default=AUDIT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    result = audit(args.output_dir, args.result, args.audit_output, overwrite=args.overwrite)
    print(json.dumps({"status": result["status"], "audit": str(args.audit_output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
