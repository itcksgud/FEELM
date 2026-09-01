from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from recommendation_protocol_v4 import sha256_file


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COLUMNS = {
    "movie-identity.parquet": ["movie_id", "imdb_id", "tmdb_id", "identity_status", "media_type", "source_updated_at", "fetched_at", "response_sha256"],
    "structured-features.parquet": ["movie_id", "tmdb_id", "original_language", "release_year", "runtime_minutes", "genre_ids", "director_ids", "top5_cast_ids", "keyword_ids", "missing_mask", "feature_eligible"],
    "text-embeddings.parquet": ["movie_id", "model_id", "model_revision", "input_text_sha256", "embedding", "l2_norm", "feature_eligible"],
    "quarantine.parquet": ["movie_id", "attempted_tmdb_id", "identity_status", "reason_code", "last_http_status"],
}


def _load_token(env_file: Path, key: str) -> str:
    if os.environ.get(key, "").strip():
        return os.environ[key].strip()
    if not env_file.is_file():
        return ""
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in raw_line or raw_line.lstrip().startswith("#"):
            continue
        name, value = raw_line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify(manifest_path: Path, *, preflight: bool, env_file: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    contract_path = REPO_ROOT / manifest["contract"]
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    _require(sha256_file(contract_path) == manifest["contract_sha256"], "contract checksum mismatch")
    _require(bool(manifest.get("preflight")) == preflight, "manifest scope differs from verifier mode")
    _require(manifest["model_revision"] == contract["embedding"]["model_revision"], "model revision mismatch")

    artifact_map: dict[str, Path] = {}
    for artifact in manifest["artifacts"]:
        path = REPO_ROOT / artifact["path"]
        _require(path.is_file(), f"artifact missing: {path}")
        _require(path.stat().st_size == artifact["bytes"], f"artifact byte size mismatch: {path.name}")
        _require(sha256_file(path) == artifact["sha256"], f"artifact checksum mismatch: {path.name}")
        artifact_map[path.name] = path
    for name in (*EXPECTED_COLUMNS, "coverage-summary.json"):
        _require(name in artifact_map, f"required artifact absent from manifest: {name}")

    tables: dict[str, pa.Table] = {}
    for name, expected in EXPECTED_COLUMNS.items():
        table = pq.read_table(artifact_map[name])
        _require(table.column_names == expected, f"schema columns differ: {name}")
        tables[name] = table
    identity = tables["movie-identity.parquet"].to_pandas()
    structured = tables["structured-features.parquet"].to_pandas()
    embeddings = tables["text-embeddings.parquet"].to_pandas()
    quarantine = tables["quarantine.parquet"].to_pandas()

    for name, frame in (("identity", identity), ("structured", structured), ("embeddings", embeddings), ("quarantine", quarantine)):
        _require(int(frame["movie_id"].duplicated().sum()) == 0, f"duplicate movie rows in {name}")
    eligible_ids = set(identity.loc[identity["identity_status"].isin(["ML_TMDB_VERIFIED", "RECOVERED_BY_IMDB"]), "movie_id"].astype(int))
    quarantine_ids = set(quarantine["movie_id"].astype(int))
    _require(eligible_ids.isdisjoint(quarantine_ids), "eligible and quarantine movie sets overlap")
    _require(eligible_ids == set(structured["movie_id"].astype(int)), "structured movie set differs from identity-eligible set")
    _require(eligible_ids == set(embeddings["movie_id"].astype(int)), "embedding movie set differs from identity-eligible set")
    _require(set(identity["movie_id"].astype(int)) == eligible_ids.union(quarantine_ids), "identity partition is incomplete")

    forbidden = set(contract["preference_feature_forbidden_fields"])
    for name in ("structured-features.parquet", "text-embeddings.parquet"):
        _require(not forbidden.intersection(tables[name].column_names), f"forbidden preference columns in {name}")
    template = contract["embedding"]["input_template"].lower()
    _require(not any(field.lower() in template for field in forbidden), "forbidden preference field in embedding template")

    dimension = int(contract["embedding"]["dimension"])
    embedding_type = tables["text-embeddings.parquet"].schema.field("embedding").type
    _require(pa.types.is_fixed_size_list(embedding_type) and embedding_type.list_size == dimension, "embedding physical type or dimension mismatch")
    _require(set(embeddings["model_id"].unique()) <= {contract["embedding"]["model_id"]}, "embedding model id mismatch")
    _require(set(embeddings["model_revision"].unique()) <= {contract["embedding"]["model_revision"]}, "embedding revision mismatch")
    norms = embeddings["l2_norm"].astype(float).to_numpy()
    tolerance = float(contract["gates"]["embedding_l2_norm_tolerance"])
    _require(bool(np.all(np.isfinite(norms))), "embedding norms contain non-finite values")
    _require(bool(np.all(np.abs(norms - 1.0) <= tolerance)), "embedding L2 normalization gate failed")

    summary = json.loads(artifact_map["coverage-summary.json"].read_text(encoding="utf-8"))
    _require(summary["selected_movies"] == len(identity), "coverage selected count mismatch")
    _require(summary["identity_coverage"]["eligible"] == len(eligible_ids), "identity coverage count mismatch")
    _require(summary["identity_coverage"]["linked_denominator"] >= len(eligible_ids), "identity linked denominator is invalid")
    _require(summary["structured_coverage"]["denominator"] == len(eligible_ids), "structured denominator mismatch")
    _require(summary["text_coverage"]["denominator"] == len(eligible_ids), "text denominator mismatch")
    if preflight:
        _require(len(identity) == int(contract["candidate_derivation"]["preflight_default_n"]), "preflight must contain exactly 100 selected linked movies")
        _require(summary["scope"] == "DETERMINISTIC_LINKED_SAMPLE_NOT_FULL_GATE_EVIDENCE", "preflight scope disclosure missing")
        _require(summary["full_gate_claimed"] is False, "preflight must not claim full evidence")
        _require(summary["cache"]["cache_hits_this_run"] > 0, "resume cache reuse was not observed")
        gates = contract["gates"]
        _require(summary["identity_coverage"]["rate"] >= gates["verified_or_recovered_identity_rate_of_linked_min"], "preflight identity health threshold failed")
        _require(summary["structured_coverage"]["rate"] >= gates["structured_feature_eligible_rate_of_identity_eligible_min"], "preflight structured health threshold failed")
        _require(summary["text_coverage"]["rate"] >= gates["text_feature_eligible_rate_of_identity_eligible_min"], "preflight text health threshold failed")
    else:
        gates = contract["gates"]
        _require(summary["base_train_linked_movies"] / summary["base_train_candidate_movies"] >= gates["movielens_tmdb_link_present_rate_min"], "MovieLens-TMDB link gate failed")
        _require(summary["identity_coverage"]["rate"] >= gates["verified_or_recovered_identity_rate_of_linked_min"], "identity coverage gate failed")
        _require(summary["structured_coverage"]["rate"] >= gates["structured_feature_eligible_rate_of_identity_eligible_min"], "structured coverage gate failed")
        _require(summary["text_coverage"]["rate"] >= gates["text_feature_eligible_rate_of_identity_eligible_min"], "text coverage gate failed")

    token = _load_token(env_file, contract["inputs"]["tmdb_auth_env"])
    if token:
        for artifact in manifest["artifacts"]:
            path = REPO_ROOT / artifact["path"]
            _require(token.encode("utf-8") not in path.read_bytes(), f"TMDB token leaked into {path.name}")
        output_root = artifact_map["coverage-summary.json"].parent
        for cache_path in output_root.joinpath("tmdb-cache").glob("*.json"):
            cache_bytes = cache_path.read_bytes()
            _require(token.encode("utf-8") not in cache_bytes, f"TMDB token leaked into cache {cache_path.name}")
            cache = json.loads(cache_bytes)
            serialized_request = json.dumps(cache.get("request", {}), ensure_ascii=False)
            _require("Authorization" not in serialized_request, f"authorization header leaked into cache request {cache_path.name}")
            _require("api_key" not in serialized_request, f"API key query leaked into cache request {cache_path.name}")

    return {
        "status": "PASS",
        "evidence_id": manifest["evidence_id"],
        "scope": "PREFLIGHT_NOT_FULL_GATE" if preflight else "FULL_GATE",
        "selected_movies": len(identity),
        "identity_eligible": len(eligible_ids),
        "structured_rate": summary["structured_coverage"]["rate"],
        "text_rate": summary["text_coverage"]["rate"],
        "embedding_dimension": dimension,
        "locked_test_opened": False,
        "product_policy_changed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify REC-EV-019B artifacts")
    parser.add_argument("--manifest", type=Path, default=REPO_ROOT / "docs/recommendation/evidence/manifests/rec-ev-019b.json")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env.local")
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.manifest.resolve(), preflight=args.preflight, env_file=args.env_file.resolve()), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(f"REC-EV-019B verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
