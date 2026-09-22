"""Export exact inference symbols and immutable v12 weights, without retraining."""
from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import time

import numpy as np
import pandas as pd

from probe_per_target_public_v2 import _content
from public_evidence_v4 import build_cohort_support
from service_model_v12 import sha256


SYMBOLS = {
    "build_evidence_rank_dataset_v1": ("DIRECT_AXES", "JOINT_AXES", "ALL_AXES", "_tokens", "_finite_positive", "_movie_info", "_metadata_lookup", "_history_counters", "_evidence"),
    "train_evidence_rank_models_v1": ("AXES", "numeric_features"),
    "public_evidence_v2": ("PublicCalibration", "public_columns", "numeric_features_v2"),
    "public_evidence_v3": ("numeric_features_v3",),
    "public_evidence_v4": ("FEATURES", "numeric_features_v4"),
    "rating_semantics_v7": ("utility",),
    "probe_per_target_public_v2": ("_history_vote_context",),
    "audit_log_direct_user8_top100": ("ids",),
    "strict_evidence_v12": ("SIDE_NAMES", "DIRECT_NAMES", "EXTRA_NAMES", "Movie", "metadata", "DirectIndex", "qualifications", "augment"),
}

IMPORTS = {
    "build_evidence_rank_dataset_v1": "from collections import defaultdict\nfrom typing import Any\n",
    "train_evidence_rank_models_v1": "",
    "public_evidence_v2": "from dataclasses import asdict, dataclass\nfrom .train_evidence_rank_models_v1 import numeric_features\n",
    "public_evidence_v3": "from .public_evidence_v2 import PublicCalibration, numeric_features_v2\n",
    "public_evidence_v4": "from .public_evidence_v2 import PublicCalibration\nfrom .public_evidence_v3 import numeric_features_v3\n",
    "rating_semantics_v7": "",
    "probe_per_target_public_v2": "from .rating_semantics_v7 import utility\n",
    "audit_log_direct_user8_top100": "",
    "strict_evidence_v12": "from collections import defaultdict\nfrom dataclasses import dataclass\nfrom .audit_log_direct_user8_top100 import ids\n",
}

CATALOG_COLUMNS = ["movie_id", "service_movie_id", "tmdb_id", "title", "release_year", "genre_ids",
    "collection_ids", "director_ids", "top5_cast_ids", "keyword_ids", "origin_country_codes",
    "production_country_codes", "tmdb_vote_count", "tmdb_vote_average", "kobis_link_status",
    "kobis_audience_cumulative", "kobis_value_valid"]


def symbol_name(node):
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return None


def export_symbols(source_dir, destination):
    destination.mkdir()
    (destination / "__init__.py").write_text('"""Frozen v12 inference-only symbol closure."""\n', encoding="utf-8")
    lineage = {}
    for module, wanted in SYMBOLS.items():
        source_path = source_dir / f"{module}.py"
        source = source_path.read_text(encoding="utf-8")
        lines = source.splitlines(keepends=True)
        nodes = {symbol_name(node): node for node in ast.parse(source).body}
        selected = []
        for name in wanted:
            node = nodes[name]
            first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
            segment = "".join(lines[first - 1:node.end_lineno]).rstrip()
            selected.append(segment)
        text = ('"""Generated exact inference symbols; edit source, not this file."""\n'
                'from __future__ import annotations\nimport numpy as np\nimport pandas as pd\n'
                + IMPORTS[module] + "\n\n" + "\n\n\n".join(selected) + "\n")
        generated = ast.parse(text)
        generated_nodes = {symbol_name(node): node for node in generated.body}
        for name in wanted:
            if ast.dump(nodes[name], include_attributes=False) != ast.dump(generated_nodes[name], include_attributes=False):
                raise ValueError(f"symbol changed: {module}.{name}")
        target = destination / f"{module}.py"
        target.write_text(text, encoding="utf-8")
        lineage[module] = {"source": str(source_path.resolve()), "bytes": source_path.stat().st_size,
                           "sha256": sha256(source_path), "symbols": list(wanted), "symbol_ast_equal": True}
    return lineage


def export(catalog_path, model_dir, references_path, output):
    if output.exists():
        raise FileExistsError(output)
    start = time.monotonic()
    metrics = json.loads((model_dir / "metrics.json").read_text())
    if metrics["experiment"] != "v12-weighted-observed-strict-direct" or metrics["sources"]["catalog"] != sha256(catalog_path):
        raise ValueError("catalog/model source mismatch")
    if metrics["public_reference_sha256"] != sha256(references_path):
        raise ValueError("frozen public reference mismatch")
    if metrics["evidence_code_sha256"] != sha256(Path(__file__).resolve().parent / "strict_evidence_v12.py"):
        raise ValueError("training direct feature implementation changed")
    for family, name in (("gbt", "gbt-per-target.json"), ("fm", "fm-per-target.npz")):
        if metrics["models"][family]["model_sha256"] != sha256(model_dir / name):
            raise ValueError("frozen model checksum mismatch")
    catalog = pd.read_parquet(catalog_path).sort_values("movie_id", kind="stable").reset_index(drop=True)
    if catalog.movie_id.duplicated().any() or catalog.tmdb_id.duplicated().any() or not catalog.movie_id.equals(catalog.service_movie_id):
        raise ValueError("catalog identity mismatch")
    refs = pd.read_parquet(references_path).set_index("tmdb_id").loc[catalog.tmdb_id].reset_index()
    with np.load(model_dir / "fm-per-target.npz", allow_pickle=False) as state:
        if list(state["feature_names"]) != metrics["features"]:
            raise ValueError("FM schema mismatch")
        factors = state["factor"].copy()
    content = _content(catalog)
    if content.shape[1] != factors.shape[0]:
        raise ValueError("FM content dimension mismatch")
    item_factors = np.asarray(content @ factors)
    axis, support, cohort_stats = build_cohort_support(catalog)
    output.mkdir(parents=True)
    catalog[CATALOG_COLUMNS].to_parquet(output / "catalog.parquet", index=False)
    refs.to_parquet(output / "public-references.parquet", index=False)
    np.save(output / "fm-item-factors.npy", item_factors, allow_pickle=False)
    np.save(output / "cohort-axis.npy", axis, allow_pickle=False)
    np.save(output / "cohort-support.npy", support, allow_pickle=False)
    for name in ("gbt-per-target.json", "fm-per-target.npz"):
        shutil.copyfile(model_dir / name, output / name)
    shutil.copyfile(model_dir / "metrics.json", output / "training-metrics.json")
    source_dir = Path(__file__).resolve().parent
    shutil.copyfile(source_dir / "service_model_v12.py", output / "service_model_v12.py")
    lineage = export_symbols(source_dir, output / "feelm_v12_vendor")
    (output / "feature-schema.json").write_text(json.dumps(metrics["features"], indent=2), encoding="utf-8")
    versions = {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scipy", "pyarrow", "xgboost")}
    (output / "requirements.txt").write_text("".join(f"{name}=={version}\n" for name, version in versions.items()), encoding="utf-8")
    (output / "example-request.json").write_text(json.dumps({"ratings": [], "dismissedMovieIds": [], "watchedMovieIds": []}, indent=2), encoding="utf-8")
    (output / "README.md").write_text(
        "# GBT / FM personal scorer — LOCAL_ONLY / NOT_ADOPTED\n\n"
        "Both supplied v12 model files are exported unchanged. This export operation does not train or prove a quality win. See training_source_run in manifest.json and the outer handoff training report for training provenance.\n"
        "From any working directory: `python -B /absolute/bundle/service_model_v12.py --request input.json --output /outside/bundle/new-result.json --model both --limit 500`.\n"
        "Keep the bundle file set immutable: no added Python bytecode or result files. Use -B when importing its runtime. Source imports disable bytecode writing and reject all unlisted files.\n"
        "Input: ratings [{movieId, score}], dismissedMovieIds, watchedMovieIds. Service IDs only. All ratings enter, without title filter or K cap.\n"
        "Actual ratings override dismiss=1. Seen/dismissed candidates are excluded. Public/direct qualification is a separate frozen policy, not a learned guarantee.\n"
        "K0 returns NEEDS_HOST_COLD_START. Unknown preference IDs raise an error, never silently drop. Catalog eligibility is frozen at 2026-09-21.\n"
        "Output is a LOCAL diagnostic envelope, NOT consumer Redis v6. No fake ALS vectors, discovery candidates, party score, DB, Redis or HDFS writes.\n"
        "Host must integrate artifact/feature loading, cold-start, discovery 2+1, party, generation/version coupling, final exclusion and atomic republish/rollback.\n"
        "Scores are within-user ranking values, not calibrated displayed stars. Reasons are empty; relation details are local diagnostics only.\n"
        "v12 development MAE worsened for both models; it is NOT approved for replacement of the serving model.\n"
        "Prepared from local MovieLens-trained models and verified TMDB/KOBIS metadata. Preserve attribution and review source usage permissions before external distribution.\n"
        "No raw MovieLens rows or private FEELM user ratings are included. Source locations, revisions and checksums are in manifest.json.\n",
        encoding="utf-8")
    files = {path.relative_to(output).as_posix(): {"bytes": path.stat().st_size, "sha256": sha256(path)}
             for path in sorted(output.rglob("*")) if path.is_file()}
    sources = {name: {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256(path)}
               for name, path in (("catalog", catalog_path), ("references", references_path), ("models", model_dir / "metrics.json"),
                                  ("exporter", Path(__file__)), ("runtime", source_dir / "service_model_v12.py"))}
    manifest = {"format": "feelm-personal-v12-local-v1", "adoption": "NOT_ADOPTED", "sharing": "LOCAL_ONLY",
        "feature_version": "v12-strict-113", "policy_version": "strict-multiple-links-r3e",
        "team_revision": "dcabdd1893e5cfa087ab89373edcf8cc1088ba7c",
        "training_performed_by_exporter": False, "training_source_run": model_dir.name,
        "python": platform.python_version(), "dependencies": versions, "sources": sources,
        "feature_schema_sha256": files["feature-schema.json"]["sha256"], "files": files,
        "inference_source_symbols": lineage,
        "precompute_source_files": {name: {"path": str(source_dir / name), "sha256": sha256(source_dir / name)}
            for name in ("probe_per_target_public_v2.py", "model_specific_features_v5.py",
                         "train_per_target_rating_pilot_v1.py", "public_evidence_v4.py")},
        "catalog_movies": len(catalog), "cohort": cohort_stats,
        "seconds": time.monotonic() - start}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("catalog", "models", "references", "output"):
        parser.add_argument(f"--{arg}", type=Path, required=True)
    args = parser.parse_args()
    result = export(args.catalog, args.models, args.references, args.output)
    print(json.dumps({"movies": result["catalog_movies"], "seconds": result["seconds"], "files": len(result["files"])}))
