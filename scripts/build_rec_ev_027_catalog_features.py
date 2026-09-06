"""Build REC-EV-027 content features from the rating-independent MovieLens catalog."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any
import zipfile

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_rec_ev_019b_features import (  # noqa: E402
    IDENTITY_ELIGIBLE,
    TERMINAL_STATUSES,
    TmdbClient,
    _embedding_table,
    _identity_table,
    _structured_table,
    _zip_member,
    build_embedding_input,
    canonical_json_bytes,
    encode_texts,
    extract_features,
    format_imdb_id,
    load_env_value,
    nonblank,
    select_recovery,
    sha256_file,
    validate_details_identity,
    write_json,
)
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUILD = ROOT / "docs/recommendation/contracts/rec-ev-027-catalog-build.json"


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def verify_pin(spec: dict[str, Any]) -> Path:
    path = resolve(str(spec["path"]))
    require(path.is_file(), f"missing pinned source: {path}")
    require(path.stat().st_size == int(spec["bytes"]), f"pinned source byte drift: {path}")
    require(sha256_file(path) == spec["sha256"], f"pinned source hash drift: {path}")
    return path


def validate_build_contract(contract: dict[str, Any]) -> None:
    require(contract["contract_id"] == "rec-ev-027-rating-independent-catalog-build-v1", "catalog-build contract ID drift")
    require(contract["status"] == "APPROVED_FOR_RATING_INDEPENDENT_CATALOG_BUILD", "catalog-build status drift")
    source = contract["sources"]["movielens_archive"]
    require(source["allowed_members"] == ["ml-32m/movies.csv", "ml-32m/links.csv"], "allowed member drift")
    require(source["forbidden_member"] == "ml-32m/ratings.csv", "ratings member guard drift")
    for key in ("movielens_archive", "rec_ev_019b_embedding_value_cache", "rec_ev_019b_contract"):
        verify_pin(contract["sources"][key])
    require(contract["catalog"]["expected_movies"] == 87585, "catalog count drift")
    require("PRIOR_FEATURE_MEMBERSHIP" in contract["catalog"]["membership_forbidden"], "prior membership guard missing")
    require(contract["canonical_processing"]["every_catalog_id"] == "RUN_IDENTITY_VALIDATION_AND_CURRENT_FEATURE_EXTRACTION", "canonical processing drift")
    require("PRODUCTION_COUNTRIES" in contract["canonical_processing"]["korean_proxy"], "Korean proxy drift")
    implementation = contract["implementation"]
    require(implementation["path"] == "scripts/build_rec_ev_027_catalog_features.py", "implementation path drift")
    require(Path(__file__).resolve().stat().st_size == int(implementation["bytes"]), "implementation byte drift")
    require(sha256_file(Path(__file__).resolve()) == implementation["sha256"], "implementation hash drift")
    helpers = implementation["transitive_helpers"]
    require(len(helpers) == 1 and helpers[0]["path"] == "scripts/build_rec_ev_019b_features.py", "helper inventory drift")
    verify_pin(helpers[0])
    runtime = contract["runtime"]
    require(runtime == {
        "python": "3.12", "numpy": "1.26.4", "pandas": "2.2.3", "pyarrow": "20.0.0",
        "onnxruntime": "1.20.1", "transformers": "4.49.0", "huggingface_hub": "0.29.2",
    }, "catalog runtime contract drift")
    require(all(contract["forbidden"].values()), "catalog-build forbidden access guard disabled")


def actual_runtime() -> dict[str, str]:
    import huggingface_hub
    import onnxruntime
    import pyarrow
    import transformers

    return {
        "python": ".".join(sys.version.split()[0].split(".")[:2]),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "pyarrow": pyarrow.__version__,
        "onnxruntime": onnxruntime.__version__,
        "transformers": transformers.__version__,
        "huggingface_hub": huggingface_hub.__version__,
    }


def derive_rating_independent_catalog(archive_path: Path) -> tuple[pd.DataFrame, list[str]]:
    """Read movies.csv and links.csv only. ratings.csv must never be opened here."""
    opened: list[str] = []
    with zipfile.ZipFile(archive_path) as archive:
        movies_member = _zip_member(archive, "movies.csv")
        links_member = _zip_member(archive, "links.csv")
        with archive.open(movies_member) as handle:
            movies = pd.read_csv(handle, usecols=["movieId"], dtype={"movieId": "int32"})
            opened.append(movies_member)
        with archive.open(links_member) as handle:
            links = pd.read_csv(
                handle,
                dtype={"movieId": "int32", "imdbId": "string", "tmdbId": "string"},
            )
            opened.append(links_member)
    if movies["movieId"].duplicated().any() or links["movieId"].duplicated().any():
        raise RuntimeError("MovieLens catalog movieId is not unique")
    links["link_row_present"] = True
    catalog = movies.merge(links, how="left", on="movieId", validate="one_to_one").rename(
        columns={"movieId": "movie_id", "imdbId": "imdb_id", "tmdbId": "tmdb_id"}
    )
    catalog["imdb_id"] = catalog["imdb_id"].map(format_imdb_id)
    catalog["tmdb_id"] = pd.to_numeric(catalog["tmdb_id"], errors="coerce").astype("Int64")
    catalog.loc[catalog["tmdb_id"] <= 0, "tmdb_id"] = pd.NA
    return catalog.sort_values("movie_id", kind="stable", ignore_index=True), opened


class LayeredTmdbClient(TmdbClient):
    """Reuse verified prior raw responses without letting cache membership define the catalog."""

    def __init__(self, *args: Any, fallback_cache_root: Path, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fallback_cache_root = fallback_cache_root
        self.fallback_hits = 0
        self._fallback_lock = threading.Lock()

    @staticmethod
    def _valid_cached(
        cached: dict[str, Any], kind: str, identity: str, endpoint: str, params: dict[str, str]
    ) -> bool:
        request = cached.get("request") or {}
        status = int(cached.get("status", 0))
        body = cached.get("body")
        return bool(
            request == {"kind": kind, "identity": str(identity), "endpoint": endpoint, "params": params}
            and (200 <= status < 300 or status in TERMINAL_STATUSES)
            and cached.get("body_sha256") == hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        )

    def _fallback(self, kind: str, identity: str, endpoint: str, params: dict[str, str]) -> dict[str, Any] | None:
        language = params.get("language", "none")
        path = self.fallback_cache_root / self._cache_name(kind, identity, language)
        if not self.resume or self.refresh or not path.is_file():
            return None
        cached = json.loads(path.read_text(encoding="utf-8"))
        if not self._valid_cached(cached, kind, identity, endpoint, params):
            return None
        cached["cache_hit"] = True
        with self._counter_lock:
            self.cache_hits += 1
        with self._fallback_lock:
            self.fallback_hits += 1
        return cached

    def request(self, kind: str, identity: str, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        language = params.get("language", "none")
        local_path = self.cache_root / self._cache_name(kind, identity, language)
        if self.resume and not self.refresh and local_path.is_file():
            cached = json.loads(local_path.read_text(encoding="utf-8"))
            if self._valid_cached(cached, kind, identity, endpoint, params):
                cached["cache_hit"] = True
                with self._counter_lock:
                    self.cache_hits += 1
                return cached
        fallback = self._fallback(kind, identity, endpoint, params)
        if fallback is not None:
            return fallback
        return super().request(kind, identity, endpoint, params)

    def cached_details(self, tmdb_id: int, language: str = "ko-KR") -> dict[str, Any]:
        params = {"append_to_response": "credits,keywords", "language": language}
        endpoint = f"/3/movie/{int(tmdb_id)}"
        return self.request("movie", str(int(tmdb_id)), endpoint, params)


def process_movie(row: dict[str, Any], client: LayeredTmdbClient) -> dict[str, Any]:
    movie_id = int(row["movie_id"])
    imdb_id = nonblank(row.get("imdb_id"))
    attempted_tmdb = row.get("tmdb_id")
    if attempted_tmdb is None or pd.isna(attempted_tmdb):
        return {
            "movie_id": movie_id, "imdb_id": imdb_id, "status": "TMDB_NOT_FOUND",
            "reason": "LINK_TMDB_ID_MISSING", "attempted_tmdb_id": None, "http_status": None,
            "production_country_codes": [],
        }
    attempted_tmdb = int(attempted_tmdb)
    primary_response = client.details(attempted_tmdb, "ko-KR")
    selected_tmdb = attempted_tmdb
    identity_status = "ML_TMDB_VERIFIED"
    reason = "LINKED_DETAILS_VERIFIED"

    if int(primary_response["status"]) in TERMINAL_STATUSES:
        if not imdb_id:
            return {
                "movie_id": movie_id, "imdb_id": imdb_id, "status": "TMDB_NOT_FOUND",
                "reason": "TERMINAL_DETAILS_WITHOUT_IMDB", "attempted_tmdb_id": attempted_tmdb,
                "http_status": primary_response["status"], "production_country_codes": [],
            }
        find_response = client.find_by_imdb(imdb_id)
        if not (200 <= int(find_response["status"]) < 300):
            return {
                "movie_id": movie_id, "imdb_id": imdb_id, "status": "IDENTITY_REVIEW_REQUIRED",
                "reason": "IMDB_RECOVERY_HTTP_FAILURE", "attempted_tmdb_id": attempted_tmdb,
                "http_status": find_response["status"], "production_country_codes": [],
            }
        action, recovered_id, reason = select_recovery(find_response["body"] or {})
        if action != "RECOVER":
            status = {"TV": "TYPE_MISMATCH_TV", "NOT_FOUND": "TMDB_NOT_FOUND"}.get(
                action, "IDENTITY_REVIEW_REQUIRED"
            )
            return {
                "movie_id": movie_id, "imdb_id": imdb_id, "status": status, "reason": reason,
                "attempted_tmdb_id": attempted_tmdb, "http_status": primary_response["status"],
                "production_country_codes": [],
            }
        selected_tmdb = int(recovered_id)
        primary_response = client.details(selected_tmdb, "ko-KR")
        identity_status = "RECOVERED_BY_IMDB"
        if not (200 <= int(primary_response["status"]) < 300):
            return {
                "movie_id": movie_id, "imdb_id": imdb_id, "status": "IDENTITY_REVIEW_REQUIRED",
                "reason": "RECOVERED_DETAILS_HTTP_FAILURE", "attempted_tmdb_id": attempted_tmdb,
                "http_status": primary_response["status"], "production_country_codes": [],
            }
    elif not (200 <= int(primary_response["status"]) < 300):
        return {
            "movie_id": movie_id, "imdb_id": imdb_id, "status": "IDENTITY_REVIEW_REQUIRED",
            "reason": "DETAILS_HTTP_FAILURE", "attempted_tmdb_id": attempted_tmdb,
            "http_status": primary_response["status"], "production_country_codes": [],
        }

    body = primary_response["body"] or {}
    valid, validation_reason = validate_details_identity(body, selected_tmdb, imdb_id)
    if not valid:
        return {
            "movie_id": movie_id, "imdb_id": imdb_id, "status": "IDENTITY_REVIEW_REQUIRED",
            "reason": validation_reason, "attempted_tmdb_id": attempted_tmdb,
            "http_status": primary_response["status"], "production_country_codes": [],
        }
    english_body = None
    if not nonblank(body.get("title")) or not nonblank(body.get("overview")):
        english_response = client.details(selected_tmdb, "en-US")
        english_status = int(english_response["status"])
        if 200 <= english_status < 300:
            english_body = english_response["body"]
        elif english_status not in TERMINAL_STATUSES:
            raise RuntimeError(
                f"nonterminal English fallback failure for movie {movie_id}: HTTP {english_status}"
            )
    structured, text = extract_features(movie_id, selected_tmdb, body, english_body)
    countries = sorted({
        nonblank(item.get("iso_3166_1")).upper()
        for item in (body.get("production_countries") or [])
        if nonblank(item.get("iso_3166_1"))
    })
    return {
        "movie_id": movie_id,
        "imdb_id": imdb_id,
        "tmdb_id": selected_tmdb,
        "status": identity_status,
        "reason": reason,
        "attempted_tmdb_id": attempted_tmdb,
        "http_status": primary_response["status"],
        "fetched_at": primary_response["fetched_at"],
        "response_sha256": primary_response["body_sha256"],
        "structured": structured,
        "text": text,
        "production_country_codes": countries,
    }


def atomic_parquet(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    pq.write_table(table, temporary, compression="zstd")
    os.replace(temporary, path)


def domain_table(records: list[dict[str, Any]]) -> pa.Table:
    schema = pa.schema([
        ("movie_id", pa.int32()),
        ("tmdb_id", pa.int32()),
        ("production_country_codes", pa.list_(pa.string())),
        ("tmdb_korean_origin_proxy", pa.bool_()),
    ])
    rows = []
    for record in records:
        if record["status"] not in IDENTITY_ELIGIBLE:
            continue
        countries = list(record["production_country_codes"])
        rows.append({
            "movie_id": int(record["movie_id"]),
            "tmdb_id": int(record["tmdb_id"]),
            "production_country_codes": countries,
            "tmdb_korean_origin_proxy": "KR" in countries,
        })
    return pa.Table.from_pylist(rows, schema=schema)


def artifact_pin(path: Path) -> dict[str, Any]:
    return {"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def reusable_embedding(
    cached: pd.Series | None, embedding_config: dict[str, Any], input_sha: str
) -> np.ndarray | None:
    if cached is None:
        return None
    vector = np.asarray(cached["embedding"], dtype=np.float32)
    valid = bool(
        cached["model_id"] == embedding_config["model_id"]
        and cached["model_revision"] == embedding_config["model_revision"]
        and cached["input_text_sha256"] == input_sha
        and vector.shape == (int(embedding_config["dimension"]),)
        and np.isfinite(vector).all()
        and abs(float(np.linalg.norm(vector)) - 1.0) <= 0.0001
    )
    return vector if valid else None


def validate_canonical_coverage(records: list[dict[str, Any]], catalog: pd.DataFrame) -> None:
    observed = [int(row["movie_id"]) for row in records]
    expected = catalog["movie_id"].astype(int).tolist()
    if observed != expected or len(observed) != len(set(observed)):
        raise RuntimeError("not every catalog movie was processed by the canonical path exactly once")


def build(args: argparse.Namespace) -> dict[str, Any]:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    validate_build_contract(contract)
    require(args.resume is bool(contract["transport"]["resume_required"]), "--resume is required by contract")
    require(args.refresh is False, "--refresh is forbidden by the immutable build contract")
    require(args.max_concurrency == int(contract["transport"]["maximum_concurrency"]), "concurrency drift")
    require(args.embedding_batch_size == int(contract["transport"]["embedding_batch_size"]), "embedding batch drift")
    runtime = actual_runtime()
    require(runtime == contract["runtime"], f"runtime version drift: {runtime}")
    build_contract = contract
    archive = verify_pin(contract["sources"]["movielens_archive"])
    old_contract_path = verify_pin(contract["sources"]["rec_ev_019b_contract"])
    old_contract = json.loads(old_contract_path.read_text(encoding="utf-8"))
    token = load_env_value(args.env_file, old_contract["inputs"]["tmdb_auth_env"])
    catalog, opened_members = derive_rating_independent_catalog(archive)
    if len(catalog) != int(build_contract["catalog"]["expected_movies"]):
        raise RuntimeError("catalog movie count drift")
    if int(catalog["link_row_present"].fillna(False).sum()) != int(build_contract["catalog"]["expected_links"]):
        raise RuntimeError("catalog link row count drift")
    if int(catalog["tmdb_id"].notna().sum()) != int(build_contract["catalog"]["expected_positive_tmdb_links"]):
        raise RuntimeError("positive TMDB link count drift")
    if any(member.endswith("ratings.csv") for member in opened_members):
        raise RuntimeError("ratings.csv opened during catalog build")

    output_root = args.output_root
    output_root.mkdir(parents=True, exist_ok=True)
    client = LayeredTmdbClient(
        token,
        output_root / "tmdb-cache",
        resume=args.resume,
        refresh=args.refresh,
        maximum_attempts=int(old_contract["transport"]["maximum_attempts"]),
        retry_schedule=tuple(old_contract["transport"]["retry_schedule_seconds"]),
        fallback_cache_root=ROOT / contract["sources"]["untrusted_raw_response_cache"],
    )
    embedding_reuse_path = verify_pin(contract["sources"]["rec_ev_019b_embedding_value_cache"])
    records: list[dict[str, Any]] = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.max_concurrency) as executor:
        futures = {executor.submit(process_movie, row, client): int(row["movie_id"]) for row in catalog.to_dict("records")}
        for index, future in enumerate(as_completed(futures), start=1):
            records.append(future.result())
            if index % 1000 == 0 or index == len(futures):
                print(f"canonical catalog TMDB: {index:,}/{len(futures):,} ({time.time() - started:.1f}s)", flush=True)
    records.sort(key=lambda row: row["movie_id"])
    validate_canonical_coverage(records, catalog)
    incomplete_http = [
        row for row in records
        if str(row.get("reason", "")).endswith("HTTP_FAILURE")
        and int(row.get("http_status") or 0) not in TERMINAL_STATUSES
    ]
    if incomplete_http:
        sample = [(row["movie_id"], row.get("reason"), row.get("http_status")) for row in incomplete_http[:10]]
        raise RuntimeError(f"nonterminal TMDB failures must be retried before freeze: {sample}")
    eligible = [row for row in records if row["status"] in IDENTITY_ELIGIBLE]

    embedding_config = old_contract["embedding"]
    reusable = pd.read_parquet(embedding_reuse_path).set_index("movie_id", verify_integrity=True)
    vectors = np.zeros((len(eligible), int(embedding_config["dimension"])), dtype=np.float32)
    text_rows: list[dict[str, Any]] = []
    encode_positions: list[int] = []
    encode_inputs: list[str] = []
    reused = 0
    for position, row in enumerate(eligible):
        input_text = build_embedding_input(
            embedding_config["input_template"], embedding_config["input_prefix"], row["text"]
        )
        input_sha = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
        text_rows.append({
            "movie_id": int(row["movie_id"]),
            "input_text_sha256": input_sha,
            "feature_eligible": bool(row["text"]["feature_eligible"]),
        })
        cached = reusable.loc[int(row["movie_id"])] if int(row["movie_id"]) in reusable.index else None
        vector = reusable_embedding(cached, embedding_config, input_sha)
        if vector is not None:
            vectors[position] = vector
            reused += 1
            continue
        encode_positions.append(position)
        encode_inputs.append(input_text)
    if encode_inputs:
        encoded = encode_texts(
            encode_inputs, embedding_config, args.embedding_batch_size,
            output_root / "new-embedding-checkpoint", args.resume,
        )
        vectors[np.asarray(encode_positions, dtype=np.int64)] = np.asarray(encoded, dtype=np.float32)
    if not np.isfinite(vectors).all() or bool((np.linalg.norm(vectors, axis=1) <= 0).any()):
        raise RuntimeError("nonfinite or zero embedding in rebuilt catalog")

    outputs = build_contract["outputs"]
    paths = {key: ROOT / value for key, value in outputs.items()}
    identity_table = _identity_table(records)
    structured_table = _structured_table([row["structured"] for row in eligible])
    embedding_table = _embedding_table(text_rows, vectors, embedding_config)
    merged_domain = domain_table(records)
    if identity_table.num_rows != int(build_contract["catalog"]["expected_movies"]):
        raise RuntimeError("identity artifact does not cover all MovieLens movies")
    atomic_parquet(identity_table, paths["identity"])
    atomic_parquet(structured_table, paths["structured"])
    atomic_parquet(embedding_table, paths["embeddings"])
    atomic_parquet(merged_domain, paths["domain_projection"])

    status_counts = pd.Series([row["status"] for row in records]).value_counts().sort_index().to_dict()
    summary = {
        "schema_version": 1,
        "evidence_id": "REC-EV-027-CATALOG",
        "status": "PASS_RATING_INDEPENDENT_FULL_CATALOG_BUILD",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "archive_members_opened": opened_members,
        "ratings_member_opened": False,
        "rating_values_opened": False,
        "catalog_movies": int(len(catalog)),
        "catalog_link_rows": int(catalog["link_row_present"].fillna(False).sum()),
        "positive_tmdb_links": int(catalog["tmdb_id"].notna().sum()),
        "attempted_movies": int(len(records)),
        "canonical_processed_movies": int(len(records)),
        "nonterminal_http_failures": 0,
        "identity_eligible": int(structured_table.num_rows),
        "structured_eligible": int(sum(bool(row["structured"]["feature_eligible"]) for row in eligible)),
        "text_eligible": int(sum(bool(row["text"]["feature_eligible"]) for row in eligible)),
        "tmdb_production_country_kr_proxy": int(sum("KR" in row["production_country_codes"] for row in eligible)),
        "status_counts": {str(key): int(value) for key, value in status_counts.items()},
        "cache": {
            "verified_prior_response_hits": int(client.fallback_hits),
            "all_cache_hits": int(client.cache_hits),
            "network_requests": int(client.network_requests),
            "cache_membership_defined_catalog": False,
            "auth_material_written": False,
        },
        "embeddings": {
            "reused_by_rebuilt_input_hash": int(reused),
            "encoded": int(len(encode_inputs)),
            "model_id": embedding_config["model_id"],
            "revision": embedding_config["model_revision"],
            "dimension": int(embedding_config["dimension"]),
        },
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
        "runtime": {**runtime, "implementation_sha256": sha256_file(Path(__file__).resolve())},
    }
    write_json(paths["summary"], summary)
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-027-CATALOG",
        "status": summary["status"],
        "catalog_build_contract_sha256": sha256_file(args.contract),
        "implementation_sha256": sha256_file(Path(__file__).resolve()),
        "implementation_artifacts": [
            artifact_pin(Path(__file__).resolve()),
            *[artifact_pin(resolve(spec["path"])) for spec in build_contract["implementation"]["transitive_helpers"]],
        ],
        "source_archive_sha256": sha256_file(archive),
        "ratings_member_opened": False,
        "artifacts": [artifact_pin(paths[key]) for key in ("identity", "structured", "embeddings", "domain_projection", "summary")],
    }
    write_json(paths["manifest"], manifest)
    return {"summary": summary, "manifest": artifact_pin(paths["manifest"]), "artifacts": manifest["artifacts"]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.local")
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/recommendation-evidence/rec-ev-027-catalog")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--max-concurrency", type=int, default=8)
    parser.add_argument("--embedding-batch-size", type=int, default=32)
    args = parser.parse_args()
    args.contract = args.contract.resolve()
    args.env_file = args.env_file.resolve()
    args.output_root = args.output_root.resolve()
    return args


def main() -> int:
    try:
        result = build(parse_args())
        print(json.dumps({"status": result["summary"]["status"], "summary": result["summary"], "manifest": result["manifest"]}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(f"REC-EV-027 catalog build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
