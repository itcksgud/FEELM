"""Map the reviewed GKT reference export onto a current service movie ID axis.

This is an offline staging step.  It never changes a database or serving
pointer and never claims candidate or pgvector readiness.  The current service
movie export remains the source of truth for service IDs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import sys
import uuid

import numpy as np
import pandas as pd


SCHEMA = "feelm-gkt-service-mapping/1"
DB_EXPORT_SCHEMA = "feelm-db-movie-export/1"
EXPECTED_QUERY = "SELECT id, tmdb_id FROM movies ORDER BY id"
INT64_MAX = 9_223_372_036_854_775_807
INT32_MAX = 2_147_483_647
REQUIRED_STAGING_ARTIFACTS = {
    "content-vectors.float32.npy",
    "geometry-arrays.npz",
    "movie-index.parquet",
    "parity.json",
    "preprocessor-arrays.npz",
    "similar-vector-export.json",
    "text-vocabulary.json",
    "top-names.json",
    "transform-contract.json",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pin(path: Path, relative_to: Path | None = None,
        logical_path: str | None = None) -> dict[str, Any]:
    logical = logical_path or (
        path.name if relative_to is None
        else path.resolve().relative_to(relative_to.resolve()).as_posix()
    )
    return {"path": logical, "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def verify_pin(path: Path, expected: Mapping[str, Any], label: str) -> None:
    require(path.is_file(), f"{label} is missing")
    require(path.stat().st_size == expected.get("bytes"), f"{label} byte count drift")
    require(sha256_file(path) == expected.get("sha256"), f"{label} SHA-256 drift")


def json_load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(payload, dict), f"{path.name} must contain a JSON object")
    return payload


def normalized_lf_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    require(not raw.startswith(b"\xef\xbb\xbf"), f"{path.name} must not contain a UTF-8 BOM")
    return raw.replace(b"\r\n", b"\n")


def parse_positive_decimal(value: str, maximum: int, field: str) -> int:
    require(bool(value) and value.isascii() and value.isdecimal(),
            f"{field} must use unsigned base-10 digits")
    parsed = int(value)
    require(0 < parsed <= maximum, f"{field} is outside its supported positive range")
    return parsed


def validate_db_export(csv_path: Path, sidecar_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    sidecar = json_load(sidecar_path)
    require(sidecar.get("schemaVersion") == DB_EXPORT_SCHEMA, "unexpected DB export sidecar schema")
    require(sidecar.get("query") == EXPECTED_QUERY, "DB export query is not the frozen v1 query")
    require(isinstance(sidecar.get("exportedAt"), str) and sidecar["exportedAt"].strip(),
            "DB export time is required")
    require(isinstance(sidecar.get("sourceName"), str) and sidecar["sourceName"].strip(),
            "non-secret DB source name is required")
    csv_pin = sidecar.get("csv")
    require(isinstance(csv_pin, dict), "sidecar.csv is required")
    require(csv_pin.get("bytes") == csv_path.stat().st_size, "DB export byte count drift")
    require(csv_pin.get("sha256") == sha256_file(csv_path), "DB export SHA-256 drift")

    rows: list[tuple[int, int | None]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("DB export is empty") from exc
        require(header == ["id", "tmdb_id"], "DB export header must be exactly id,tmdb_id")
        for line_number, row in enumerate(reader, 2):
            require(len(row) == 2, f"DB export line {line_number} must contain exactly two columns")
            service_id = parse_positive_decimal(row[0], INT64_MAX, f"line {line_number} id")
            tmdb_id = None if row[1] == "" else parse_positive_decimal(
                row[1], INT32_MAX, f"line {line_number} tmdb_id")
            rows.append((service_id, tmdb_id))
    require(len(rows) > 0, "DB export contains no movie rows")
    require(sidecar.get("rows") == len(rows), "DB export row count differs from sidecar")
    frame = pd.DataFrame(rows, columns=["serviceMovieId", "tmdbId"])
    require(frame["serviceMovieId"].is_unique, "DB export contains duplicate service movie IDs")
    present_tmdb = frame["tmdbId"].dropna()
    require(present_tmdb.is_unique, "DB export maps one TMDB ID to multiple service movie IDs")
    return frame.sort_values("serviceMovieId", kind="stable").reset_index(drop=True), sidecar


def validate_reference(staging_dir: Path, float64_content_path: Path) -> tuple[dict[str, Any], pd.DataFrame, np.ndarray, np.ndarray]:
    manifest_path = staging_dir / "manifest.json"
    manifest = json_load(manifest_path)
    require(manifest.get("schemaVersion") == "feelm-gkt-staging-bundle/1",
            "unexpected GKT staging schema")
    require(manifest.get("status") == "STAGING_ID_MAPPING_REQUIRED",
            "input GKT bundle is not the reviewed ID-mapping stage")
    require(manifest.get("readyForServing") is False, "input GKT staging must not claim serving readiness")
    require(manifest.get("legacyPickleExported") is False, "legacy pickle export is forbidden")
    require(manifest.get("pairwiseNeighborsGenerated") is False, "pairwise neighbor export is forbidden")
    artifacts = manifest.get("artifacts")
    require(isinstance(artifacts, dict), "staging artifact pins are required")
    require(set(artifacts) == REQUIRED_STAGING_ARTIFACTS,
            "staging artifact allowlist is incomplete or contains unexpected files")
    for name, expected in artifacts.items():
        path = staging_dir / name
        require(expected.get("path") == name, f"staging artifact logical path mismatch: {name}")
        verify_pin(path, expected, f"staging artifact {name}")

    index = pd.read_parquet(staging_dir / "movie-index.parquet")
    required_columns = {
        "rowIndex", "referenceServiceMovieId", "tmdbId", "tasteId", "childId", "groupId",
        "topSupported", "subSupported", "referenceIdMapped", "referenceGroupMapped",
        "serviceIdMapped", "groupMapped",
    }
    require(set(index.columns) == required_columns, "unexpected staging movie-index columns")
    require(not bool(index.isna().any().any()), "staging movie-index must not contain nulls")
    for column in ("rowIndex", "referenceServiceMovieId", "tmdbId", "tasteId", "childId", "groupId"):
        require(pd.api.types.is_integer_dtype(index[column]), f"staging {column} must be integer")
    rows = manifest.get("rowCounts", {}).get("rows")
    dimensions = manifest.get("rowCounts", {}).get("dimensions")
    require(rows == len(index), "staging movie-index row count drift")
    require(dimensions == 131, "staging vector dimensions must be 131")
    require(index["rowIndex"].tolist() == list(range(len(index))), "staging rowIndex must be contiguous")
    require(index["tmdbId"].is_unique and bool((index["tmdbId"] > 0).all()),
            "staging TMDB IDs must be positive and unique")
    require(index["referenceServiceMovieId"].is_unique
            and bool((index["referenceServiceMovieId"] > 0).all()),
            "reference service IDs must be positive and unique")
    require(not bool(index["serviceIdMapped"].any()) and not bool(index["groupMapped"].any()),
            "staging index already claims current service mapping")
    require(bool(index["tasteId"].between(0, 7).all()), "staging taste IDs must be in [0,7]")
    require(bool(index["childId"].between(0, 127).all()), "staging child IDs must be in [0,127]")
    require(bool(index["groupId"].between(0, 1023).all()), "staging group IDs must be in [0,1023]")
    require(bool((index["groupId"] == index["tasteId"] * 128 + index["childId"]).all()),
            "staging group ID formula mismatch")
    for column in ("topSupported", "subSupported", "referenceIdMapped",
                   "referenceGroupMapped", "serviceIdMapped", "groupMapped"):
        require(index[column].dtype == bool, f"staging {column} must be boolean")
    require(bool(index["referenceIdMapped"].all()), "all reference IDs must be mapped")
    expected_reference_group = (
        index["referenceIdMapped"] & index["topSupported"] & index["subSupported"]
    )
    require(bool((index["referenceGroupMapped"] == expected_reference_group).all()),
            "reference group support flags are inconsistent")

    parity = json_load(staging_dir / "parity.json")
    for check in ("G01", "G02", "G05"):
        require(parity.get(check, {}).get("status") == "PASS",
                f"staging parity {check} must be PASS")
    require(parity.get("S01", {}).get("status") == "PARTIAL_PASS",
            "staging S01 must be PARTIAL_PASS before ID mapping")

    float32_vectors: np.ndarray | None = None
    float64_vectors: np.ndarray | None = None
    try:
        float32_vectors = np.load(staging_dir / "content-vectors.float32.npy", mmap_mode="r")
        require(float32_vectors.dtype == np.float32 and float32_vectors.shape == (len(index), 131),
                "staging float32 vector shape or dtype mismatch")
        require(bool(np.isfinite(float32_vectors).all()), "staging float32 reference contains non-finite values")
        source = manifest.get("sources", {}).get("referenceContentVectors")
        require(isinstance(source, dict), "float64 reference source pin is required")
        require(source.get("bytes") == float64_content_path.stat().st_size,
                "float64 reference byte count drift")
        require(source.get("sha256") == sha256_file(float64_content_path),
                "float64 reference SHA-256 drift")
        float64_vectors = np.load(float64_content_path, mmap_mode="r")
        require(float64_vectors.dtype == np.float64 and float64_vectors.shape == (len(index), 131),
                "float64 reference vector shape or dtype mismatch")
        require(bool(np.isfinite(float64_vectors).all()), "float64 reference contains non-finite values")
        require(bool(np.array_equal(float32_vectors, float64_vectors.astype(np.float32))),
                "float32 vectors do not match the reviewed float64 source")
        squared_norms = np.einsum(
            "ij,ij->i", float64_vectors, float64_vectors, dtype=np.float64, optimize=False)
        top_squared_norms = np.einsum(
            "ij,ij->i", float64_vectors[:, :19], float64_vectors[:, :19],
            dtype=np.float64, optimize=False)
        sub_supported = squared_norms > 1e-12
        top_supported = top_squared_norms > 1e-12
        norms = np.sqrt(squared_norms)
        float32_norms = np.sqrt(np.einsum(
            "ij,ij->i", float32_vectors, float32_vectors, dtype=np.float64, optimize=False))
        require(bool(np.max(np.abs(norms[sub_supported] - 1.0), initial=0.0) <= 1e-12),
                "supported float64 content vectors must be unit normalized")
        require(bool(np.max(np.abs(norms[~sub_supported]), initial=0.0) <= 1e-12),
                "unsupported float64 content vectors must be zero")
        require(bool(np.max(np.abs(float32_norms[sub_supported] - 1.0), initial=0.0) <= 1e-6),
                "supported float32 content vectors exceed the 1e-6 norm tolerance")
        require(bool(np.max(np.abs(float32_norms[~sub_supported]), initial=0.0) <= 1e-12),
                "unsupported float32 content vectors must be zero")
        require(bool(np.array_equal(index["subSupported"].to_numpy(), sub_supported)),
                "subSupported differs from the float64 vector support rule")
        require(bool(np.array_equal(index["topSupported"].to_numpy(), top_supported)),
                "topSupported differs from the float64 genre block support rule")
        return manifest, index, float32_vectors, float64_vectors
    except BaseException:
        close_memmap(float32_vectors)
        close_memmap(float64_vectors)
        raise


def close_memmap(value: np.ndarray | None) -> None:
    mmap = getattr(value, "_mmap", None)
    if mmap is not None:
        mmap.close()


def validate_recipe_equivalence(source_recipe: Path, team_recipe: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    source_raw = sha256_file(source_recipe)
    team_raw = sha256_file(team_recipe)
    require(source_raw == manifest.get("groupRecipeSha256"), "source recipe hash differs from staging manifest")
    source_lf = normalized_lf_bytes(source_recipe)
    team_lf = normalized_lf_bytes(team_recipe)
    require(source_lf == team_lf, "team recipe differs from source beyond CRLF/LF normalization")
    normalized_hash = hashlib.sha256(source_lf).hexdigest()
    return {
        "equivalence": "CRLF_TO_LF_ONLY" if source_recipe.read_bytes() != team_recipe.read_bytes() else "BYTE_EXACT",
        "sourceRawSha256": source_raw,
        "teamRawSha256": team_raw,
        "normalizedLfSha256": normalized_hash,
    }


def vector_hash(row: np.ndarray) -> str:
    require(row.dtype == np.float64 and row.shape == (131,), "vector hash input must be float64[131]")
    digest = hashlib.sha256()
    digest.update(b"float64|131|C|")
    digest.update(np.ascontiguousarray(row, dtype="<f8").tobytes(order="C"))
    return digest.hexdigest()


def source_link_hash(source_catalog_sha: str, reference_manifest_sha: str,
                     row_index: int, tmdb_id: int) -> str:
    logical = f"{source_catalog_sha}|{reference_manifest_sha}|{row_index}|{tmdb_id}"
    return hashlib.sha256(logical.encode("utf-8")).hexdigest()


def publish_directory(temporary: Path, output_dir: Path) -> None:
    require(not output_dir.exists(), "output directory already exists; versions are immutable")
    os.replace(temporary, output_dir)


def finalize(
    staging_dir: Path,
    float64_content_path: Path,
    source_recipe: Path,
    team_recipe: Path,
    db_export_csv: Path,
    db_export_sidecar: Path,
    output_dir: Path,
    target_catalog_version: str,
) -> dict[str, Any]:
    require(bool(target_catalog_version.strip()), "target catalog version is required")
    require(not output_dir.exists(), "output directory already exists; versions are immutable")
    temporary = output_dir.parent / f".{output_dir.name}.tmp-{uuid.uuid4().hex}"
    require(not temporary.exists(), "temporary output collision")
    temporary.mkdir(parents=True)
    float32_vectors: np.ndarray | None = None
    float64_vectors: np.ndarray | None = None
    reload32: np.ndarray | None = None
    reload64: np.ndarray | None = None
    try:
        initial_input_pins = {
            "referenceManifest": pin(
                staging_dir / "manifest.json", logical_path="reference-staging/manifest.json"),
            "float64Content": pin(
                float64_content_path, logical_path="reference-source/content.float64.npy"),
            "sourceRecipe": pin(source_recipe, logical_path="recipes/source-group-recipe.json"),
            "teamRecipe": pin(team_recipe, logical_path="recipes/team-group-recipe.json"),
            "dbExport": pin(db_export_csv, logical_path="db-export/movies.csv"),
            "dbSidecar": pin(db_export_sidecar, logical_path="db-export/sidecar.json"),
            "implementation": pin(Path(__file__), logical_path="implementation/finalize_gkt_service_mapping_v1.py"),
        }
        manifest, reference, float32_vectors, float64_vectors = validate_reference(
            staging_dir, float64_content_path)
        recipe = validate_recipe_equivalence(source_recipe, team_recipe, manifest)
        db, sidecar = validate_db_export(db_export_csv, db_export_sidecar)
        reference_manifest_sha = sha256_file(staging_dir / "manifest.json")
        reference_catalog_sha = manifest["sources"]["referenceCatalog"]["sha256"]

        audit = db.merge(
            reference[["rowIndex", "referenceServiceMovieId", "tmdbId"]],
            on="tmdbId",
            how="left",
            validate="many_to_one",
        )
        audit["mappingStatus"] = np.where(
            audit["tmdbId"].isna(),
            "TMDB_ID_MISSING",
            np.where(audit["rowIndex"].isna(), "REFERENCE_NOT_FOUND", "MATCHED"),
        )
        audit["reason"] = np.where(
            audit["mappingStatus"] == "MATCHED", "",
            np.where(audit["mappingStatus"] == "TMDB_ID_MISSING",
                     "CURRENT_DB_TMDB_ID_IS_NULL", "TMDB_ID_NOT_IN_REFERENCE_CATALOG"),
        )
        audit["tmdbId"] = audit["tmdbId"].astype("Int64")
        audit["rowIndex"] = audit["rowIndex"].astype("Int64")
        audit["referenceServiceMovieId"] = audit["referenceServiceMovieId"].astype("Int64")
        audit.to_parquet(temporary / "mapping-audit.parquet", index=False)

        matched_audit = audit[audit["mappingStatus"] == "MATCHED"].copy()
        require(len(matched_audit) > 0, "no current DB movie maps to the reviewed reference catalog")
        matched_audit["rowIndex"] = matched_audit["rowIndex"].astype("int64")
        matched_audit["tmdbId"] = matched_audit["tmdbId"].astype("int64")
        matched_audit["referenceServiceMovieId"] = matched_audit["referenceServiceMovieId"].astype("int64")
        matched_audit.sort_values("serviceMovieId", kind="stable", inplace=True)
        source_rows = matched_audit["rowIndex"].to_numpy(dtype=np.int64)
        mapped_f32 = np.asarray(float32_vectors[source_rows], dtype=np.float32)
        mapped_f64 = np.asarray(float64_vectors[source_rows], dtype=np.float64)
        np.save(temporary / "content-vectors.float32.npy", mapped_f32, allow_pickle=False)
        np.save(temporary / "content-vectors.float64.npy", mapped_f64, allow_pickle=False)

        ref_by_row = reference.set_index("rowIndex", verify_integrity=True).loc[source_rows]
        assignments = pd.DataFrame({
            "mappedRowIndex": np.arange(len(source_rows), dtype=np.int64),
            "referenceRowIndex": source_rows,
            "referenceServiceMovieId": matched_audit["referenceServiceMovieId"].to_numpy(dtype=np.int64),
            "serviceMovieId": matched_audit["serviceMovieId"].to_numpy(dtype=np.int64),
            "tmdbId": matched_audit["tmdbId"].to_numpy(dtype=np.int64),
            "catalogVersion": target_catalog_version,
            "geometryVersion": manifest["geometryVersion"],
            "groupRecipeHash": recipe["teamRawSha256"],
            "tasteId": ref_by_row["tasteId"].to_numpy(dtype=np.int32),
            "childId": ref_by_row["childId"].to_numpy(dtype=np.int32),
            "groupId": ref_by_row["groupId"].to_numpy(dtype=np.int32),
            "topSupported": ref_by_row["topSupported"].to_numpy(dtype=bool),
            "subSupported": ref_by_row["subSupported"].to_numpy(dtype=bool),
            "serviceIdMapped": True,
        })
        assignments["groupMapped"] = assignments["topSupported"] & assignments["subSupported"]
        assignments["similaritySupported"] = assignments["subSupported"]
        assignments["candidateEligible"] = pd.Series(pd.NA, index=assignments.index, dtype="boolean")
        assignments["vectorHash"] = [vector_hash(row) for row in mapped_f64]
        assignments["sourceHash"] = [
            source_link_hash(reference_catalog_sha, reference_manifest_sha, int(row), int(tmdb))
            for row, tmdb in zip(source_rows, assignments["tmdbId"], strict=True)
        ]
        require(bool((assignments["groupId"] ==
                      assignments["tasteId"] * 128 + assignments["childId"]).all()),
                "mapped group ID formula mismatch")
        require(assignments["serviceMovieId"].is_unique and assignments["tmdbId"].is_unique,
                "mapped assignment IDs must be one-to-one")
        require(assignments["candidateEligible"].isna().all(),
                "candidate eligibility must remain unknown without its snapshot")
        assignments.to_parquet(temporary / "movie-assignments.parquet", index=False)

        db_tmdb = set(int(value) for value in db["tmdbId"].dropna())
        reference_tmdb = set(int(value) for value in reference["tmdbId"])
        counts = {
            "currentDbRows": len(db),
            "currentDbTmdbMissingRows": int(db["tmdbId"].isna().sum()),
            "matchedRows": len(assignments),
            "currentDbReferenceNotFoundRows": int((audit["mappingStatus"] == "REFERENCE_NOT_FOUND").sum()),
            "referenceNotInCurrentDbRows": len(reference_tmdb - db_tmdb),
            "topSupportedRows": int(assignments["topSupported"].sum()),
            "subSupportedRows": int(assignments["subSupported"].sum()),
            "groupMappedRows": int(assignments["groupMapped"].sum()),
            "similaritySupportedRows": int(assignments["similaritySupported"].sum()),
            "candidateEligibleRows": None,
        }
        require(counts["matchedRows"] + counts["currentDbTmdbMissingRows"]
                + counts["currentDbReferenceNotFoundRows"] == counts["currentDbRows"],
                "mapping status partition mismatch")

        artifacts = {
            path.name: pin(path, temporary)
            for path in sorted(temporary.iterdir(), key=lambda item: item.name)
            if path.is_file()
        }
        output_manifest = {
            "schemaVersion": SCHEMA,
            "status": "ID_MAPPED_STAGING",
            "readyForServing": False,
            "groupBundleStatus": "CANDIDATE_ELIGIBILITY_REQUIRED",
            "similarityStatus": "PGVECTOR_LOAD_REQUIRED",
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "targetCatalogVersion": target_catalog_version,
            "geometryVersion": manifest["geometryVersion"],
            "referenceStaging": {
                "manifest": initial_input_pins["referenceManifest"],
                "stagingVersion": manifest["stagingVersion"],
                "sourceCatalogVersion": manifest["sourceCatalogVersion"],
            },
            "inputs": {
                "float64Content": initial_input_pins["float64Content"],
                "sourceRecipe": initial_input_pins["sourceRecipe"],
                "teamRecipe": initial_input_pins["teamRecipe"],
            },
            "recipe": recipe,
            "dbExport": {
                "csv": initial_input_pins["dbExport"],
                "sidecar": initial_input_pins["dbSidecar"],
                "sourceName": sidecar["sourceName"],
                "query": sidecar["query"],
                "exportedAt": sidecar["exportedAt"],
            },
            "rowCounts": counts,
            "vectorContract": {
                "float64": "GROUP_PROFILE_SOURCE_DO_NOT_DERIVE_FROM_FLOAT32",
                "float32": "PGVECTOR_COSINE_COPY",
                "dimensions": 131,
                "join": "movieAssignments.mappedRowIndex == vector row",
                "vectorHash": "SHA256(float64|131|C|little-endian-float64-row-bytes)",
                "sourceHash": "SHA256(referenceCatalogSha|referenceManifestSha|referenceRowIndex|tmdbId)",
            },
            "candidateEligibility": "UNKNOWN_NOT_FALSE",
            "runtime": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "numpy": np.__version__,
                "pandas": pd.__version__,
                "executableName": Path(sys.executable).name,
            },
            "implementation": initial_input_pins["implementation"],
            "validation": {
                "requiredStagingArtifacts": sorted(REQUIRED_STAGING_ARTIFACTS),
                "referenceParity": {"G01": "PASS", "G02": "PASS", "G05": "PASS", "S01": "PARTIAL_PASS"},
                "idMapping": "ONE_TO_ONE_VALIDATED",
                "float32MatchesFloat64Cast": True,
                "float64SupportFlagsRecomputed": True,
                "groupIdFormulaValidated": True,
                "candidateEligibilityAttached": False,
                "targetDatabaseTested": False,
                "sourcePinsVerifiedImmediatelyBeforePublish": True,
            },
            "artifacts": artifacts,
            "remainingBeforeServing": [
                "attach same-catalog candidate eligibility and full per-group T/K source orders",
                "execute discovery P11-P14/P18/G07 against actual snapshot",
                "load vector(131), create partial HNSW, and execute S02-S08 on target PostgreSQL",
                "verify Spring/API/cache and activate an immutable version pointer",
            ],
        }
        (temporary / "manifest.json").write_text(
            json.dumps(output_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

        reloaded = pd.read_parquet(temporary / "movie-assignments.parquet")
        reloaded_audit = pd.read_parquet(temporary / "mapping-audit.parquet")
        reload32 = np.load(temporary / "content-vectors.float32.npy", mmap_mode="r")
        reload64 = np.load(temporary / "content-vectors.float64.npy", mmap_mode="r")
        pd.testing.assert_frame_equal(reloaded, assignments, check_exact=True)
        pd.testing.assert_frame_equal(reloaded_audit, audit, check_exact=True)
        require(reload32.dtype == np.float32 and reload32.shape == (len(assignments), 131),
                "published float32 vector reload mismatch")
        require(reload64.dtype == np.float64 and reload64.shape == (len(assignments), 131),
                "published float64 vector reload mismatch")
        require(bool(np.array_equal(reload32, mapped_f32)), "published float32 vectors changed")
        require(bool(np.array_equal(reload64, mapped_f64)), "published float64 vectors changed")
        require(bool(np.array_equal(reload32, reload64.astype(np.float32))),
                "published float32 vectors differ from the float64 cast")
        require(reloaded["vectorHash"].tolist() == [vector_hash(row) for row in reload64],
                "published per-row vector hashes changed")
        require(reloaded["sourceHash"].tolist() == [
            source_link_hash(reference_catalog_sha, reference_manifest_sha, int(row), int(tmdb))
            for row, tmdb in zip(reloaded["referenceRowIndex"], reloaded["tmdbId"], strict=True)
        ], "published per-row source hashes changed")
        for name, expected in output_manifest["artifacts"].items():
            verify_pin(temporary / name, expected, f"output artifact {name}")
        current_input_paths = {
            "referenceManifest": staging_dir / "manifest.json",
            "float64Content": float64_content_path,
            "sourceRecipe": source_recipe,
            "teamRecipe": team_recipe,
            "dbExport": db_export_csv,
            "dbSidecar": db_export_sidecar,
            "implementation": Path(__file__),
        }
        for name, path in current_input_paths.items():
            verify_pin(path, initial_input_pins[name], f"input {name}")
        for name, expected in manifest["artifacts"].items():
            verify_pin(staging_dir / name, expected, f"input staging artifact {name}")
        close_memmap(reload32)
        close_memmap(reload64)
        reload32 = None
        reload64 = None
        close_memmap(float32_vectors)
        close_memmap(float64_vectors)
        float32_vectors = None
        float64_vectors = None
        publish_directory(temporary, output_dir)
        return output_manifest
    except BaseException:
        close_memmap(reload32)
        close_memmap(reload64)
        close_memmap(float32_vectors)
        close_memmap(float64_vectors)
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--staging-dir", type=Path, required=True)
    result.add_argument("--float64-content", type=Path, required=True)
    result.add_argument("--source-recipe", type=Path, required=True)
    result.add_argument("--team-recipe", type=Path, required=True)
    result.add_argument("--db-export", type=Path, required=True)
    result.add_argument("--db-sidecar", type=Path, required=True)
    result.add_argument("--output-dir", type=Path, required=True)
    result.add_argument("--catalog-version", required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    result = finalize(
        args.staging_dir,
        args.float64_content,
        args.source_recipe,
        args.team_recipe,
        args.db_export,
        args.db_sidecar,
        args.output_dir,
        args.catalog_version,
    )
    print(json.dumps({"status": result["status"], "output": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
