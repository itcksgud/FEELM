from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

from recommendation_protocol_v4 import sha256_file, user_bucket


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = Path(r"C:\higher\projects\MM\data\raw\ml-32m.zip")
BASE_URL = "https://api.themoviedb.org"
SAMPLE_SALT = "feelm-rec-019b-sample-v1"
IDENTITY_ELIGIBLE = {"ML_TMDB_VERIFIED", "RECOVERED_BY_IMDB"}
TERMINAL_STATUSES = {404, 410}
RETRY_STATUSES = {429, 500, 502, 503, 504}

MASK_ORIGINAL_LANGUAGE = 1
MASK_RELEASE_YEAR = 2
MASK_RUNTIME = 4
MASK_GENRES = 8
MASK_DIRECTORS = 16
MASK_CAST = 32
MASK_KEYWORDS = 64
MASK_OVERVIEW = 128
MASK_TITLE = 256


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def nonblank(value: Any) -> str:
    return str(value).strip() if value is not None and str(value).strip() else ""


def first_nonblank(*values: Any) -> str:
    for value in values:
        text = nonblank(value)
        if text:
            return text
    return ""


def unique_ints(items: Iterable[dict[str, Any]], *, limit: int | None = None) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for item in items:
        try:
            value = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        result.append(value)
        if limit is not None and len(result) >= limit:
            break
    return result


def format_imdb_id(value: Any) -> str:
    text = nonblank(value)
    if not text:
        return ""
    if text.startswith("tt"):
        return text
    try:
        return f"tt{int(float(text)):07d}"
    except ValueError:
        return text


def movie_sample_digest(movie_id: int) -> bytes:
    return hashlib.sha256(f"{SAMPLE_SALT}|{int(movie_id)}".encode("utf-8")).digest()


def _zip_member(archive: zipfile.ZipFile, suffix: str) -> str:
    matches = [name for name in archive.namelist() if name.endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {suffix} member, found {matches}")
    return matches[0]


def load_env_value(path: Path, key: str) -> str:
    if key in os.environ and os.environ[key].strip():
        return os.environ[key].strip()
    if not path.is_file():
        return ""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def derive_candidate_movies(archive_path: Path, chunksize: int = 1_000_000) -> tuple[pd.DataFrame, int]:
    base_movies: set[int] = set()
    scanned = 0
    bucket_lookup = np.fromiter((user_bucket(user_id) for user_id in range(300_001)), dtype=np.uint8, count=300_001)
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(_zip_member(archive, "ratings.csv")) as handle:
            for chunk in pd.read_csv(handle, usecols=["userId", "movieId"], dtype="int32", chunksize=chunksize):
                user_ids = chunk["userId"].to_numpy(dtype=np.int64, copy=False)
                if user_ids.max(initial=0) >= len(bucket_lookup):
                    raise RuntimeError("MovieLens user id exceeds the locked bucket lookup range")
                buckets = bucket_lookup[user_ids]
                base_movies.update(int(value) for value in chunk.loc[buckets <= 39, "movieId"].unique())
                scanned += len(chunk)
                if scanned % (chunksize * 8) == 0:
                    print(f"Base-Train scan: {scanned:,} ratings", flush=True)
        with archive.open(_zip_member(archive, "links.csv")) as handle:
            links = pd.read_csv(handle, dtype={"movieId": "int32", "imdbId": "string", "tmdbId": "string"})
    candidates = pd.DataFrame({"movie_id": sorted(base_movies)})
    links = links.rename(columns={"movieId": "movie_id", "imdbId": "imdb_id", "tmdbId": "tmdb_id"})
    candidates = candidates.merge(links, how="left", on="movie_id", validate="one_to_one")
    candidates["imdb_id"] = candidates["imdb_id"].map(format_imdb_id)
    candidates["tmdb_id"] = pd.to_numeric(candidates["tmdb_id"], errors="coerce").astype("Int64")
    candidates.loc[candidates["tmdb_id"] <= 0, "tmdb_id"] = pd.NA
    return candidates, scanned


class TmdbClient:
    def __init__(
        self,
        token: str,
        cache_root: Path,
        *,
        resume: bool,
        refresh: bool,
        maximum_attempts: int = 5,
        retry_schedule: tuple[float, ...] = (1, 2, 4, 8, 16),
        get: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise RuntimeError("TMDB_READ_ACCESS_TOKEN is not configured")
        self._token = token
        self.cache_root = cache_root
        self.resume = resume
        self.refresh = refresh
        self.maximum_attempts = maximum_attempts
        self.retry_schedule = retry_schedule
        self._get = get or requests.get
        self._sleep = sleep
        self._local = threading.local()
        self._counter_lock = threading.Lock()
        self.cache_hits = 0
        self.network_requests = 0

    @staticmethod
    def _cache_name(kind: str, identity: str, language: str) -> str:
        safe_identity = "".join(character for character in str(identity) if character.isalnum() or character in "-_")
        safe_language = language.replace("-", "_")
        return f"{kind}-{safe_identity}-{safe_language}.json"

    def request(self, kind: str, identity: str, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        language = params.get("language", "none")
        cache_path = self.cache_root / self._cache_name(kind, identity, language)
        if self.resume and not self.refresh and cache_path.is_file():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if int(cached.get("status", 0)) in set(range(200, 300)).union(TERMINAL_STATUSES):
                cached["cache_hit"] = True
                with self._counter_lock:
                    self.cache_hits += 1
                return cached

        url = f"{BASE_URL}{endpoint}"
        last_status = 0
        last_body: Any = None
        fetched_at = datetime.now(timezone.utc).isoformat()
        for attempt in range(self.maximum_attempts):
            try:
                request_params = dict(params)
                headers = {"accept": "application/json"}
                if self._token.startswith("ey") and self._token.count(".") == 2:
                    headers["Authorization"] = f"Bearer {self._token}"
                else:
                    request_params["api_key"] = self._token
                response = self._get(
                    url,
                    params=request_params,
                    headers=headers,
                    timeout=30,
                )
                with self._counter_lock:
                    self.network_requests += 1
                last_status = int(response.status_code)
                try:
                    last_body = response.json()
                except Exception:
                    last_body = {"non_json_response": True}
                if 200 <= last_status < 300 or last_status in TERMINAL_STATUSES:
                    break
                if last_status not in RETRY_STATUSES:
                    break
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else self.retry_schedule[min(attempt, len(self.retry_schedule) - 1)]
                self._sleep(delay)
            except requests.RequestException as error:
                last_body = {"transport_error": type(error).__name__}
                if attempt + 1 < self.maximum_attempts:
                    self._sleep(self.retry_schedule[min(attempt, len(self.retry_schedule) - 1)])
        record = {
            "schema_version": 1,
            "request": {"kind": kind, "identity": str(identity), "endpoint": endpoint, "params": params},
            "status": last_status,
            "fetched_at": fetched_at,
            "body": last_body,
            "body_sha256": hashlib.sha256(canonical_json_bytes(last_body)).hexdigest(),
            "attempts_exhausted": last_status in RETRY_STATUSES or last_status == 0,
            "cache_hit": False,
        }
        self.cache_root.mkdir(parents=True, exist_ok=True)
        write_json(cache_path, record)
        return record

    def details(self, tmdb_id: int, language: str) -> dict[str, Any]:
        return self.request(
            "movie",
            str(tmdb_id),
            f"/3/movie/{tmdb_id}",
            {"append_to_response": "credits,keywords", "language": language},
        )

    def find_by_imdb(self, imdb_id: str) -> dict[str, Any]:
        return self.request(
            "find",
            imdb_id,
            f"/3/find/{imdb_id}",
            {"external_source": "imdb_id", "language": "en-US"},
        )


def validate_details_identity(body: dict[str, Any], expected_tmdb_id: int, imdb_id: str) -> tuple[bool, str]:
    try:
        if int(body.get("id")) != int(expected_tmdb_id):
            return False, "TMDB_ID_MISMATCH"
    except (TypeError, ValueError):
        return False, "TMDB_ID_MISSING"
    returned_imdb = nonblank(body.get("imdb_id"))
    if imdb_id and returned_imdb and returned_imdb != imdb_id:
        return False, "IMDB_ID_MISMATCH"
    return True, "VERIFIED"


def select_recovery(find_body: dict[str, Any]) -> tuple[str, int | None, str]:
    movies = find_body.get("movie_results") or []
    tv = find_body.get("tv_results") or []
    if len(movies) == 1:
        try:
            return "RECOVER", int(movies[0]["id"]), "SINGLE_MOVIE_RESULT"
        except (KeyError, TypeError, ValueError):
            return "REVIEW", None, "RECOVERY_MOVIE_ID_INVALID"
    if len(movies) > 1:
        return "REVIEW", None, "AMBIGUOUS_MOVIE_RESULTS"
    if tv:
        return "TV", None, "IMDB_RESOLVES_TO_TV"
    return "NOT_FOUND", None, "NO_EXTERNAL_ID_RESULT"


def keyword_items(body: dict[str, Any]) -> list[dict[str, Any]]:
    keywords = body.get("keywords") or {}
    return list(keywords.get("keywords") or keywords.get("results") or [])


def merge_text(primary: dict[str, Any], english: dict[str, Any] | None) -> dict[str, Any]:
    english = english or {}
    genres = primary.get("genres") or []
    credits = primary.get("credits") or {}
    directors = [item for item in (credits.get("crew") or []) if item.get("job") == "Director"]
    cast = sorted(credits.get("cast") or [], key=lambda item: (int(item.get("order", 1_000_000)), int(item.get("id", 1_000_000))))
    return {
        "display_title": first_nonblank(primary.get("title"), english.get("title"), primary.get("original_title"), english.get("original_title")),
        "overview_fallback": first_nonblank(primary.get("overview"), english.get("overview")),
        "genre_names": [nonblank(item.get("name")) for item in genres if nonblank(item.get("name"))],
        "director_names": [nonblank(item.get("name")) for item in directors if nonblank(item.get("name"))],
        "top5_cast_names": [nonblank(item.get("name")) for item in cast[:5] if nonblank(item.get("name"))],
        "keyword_names": [nonblank(item.get("name")) for item in keyword_items(primary) if nonblank(item.get("name"))],
    }


def extract_features(movie_id: int, tmdb_id: int, primary: dict[str, Any], english: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    language = nonblank(primary.get("original_language"))
    release_date = nonblank(primary.get("release_date"))
    try:
        release_year = int(release_date[:4]) if len(release_date) >= 4 else None
    except ValueError:
        release_year = None
    try:
        runtime = int(primary.get("runtime")) if primary.get("runtime") is not None else None
        if runtime is not None and runtime <= 0:
            runtime = None
    except (TypeError, ValueError):
        runtime = None
    genres = unique_ints(primary.get("genres") or [])
    credits = primary.get("credits") or {}
    directors = unique_ints(item for item in (credits.get("crew") or []) if item.get("job") == "Director")
    cast_items = sorted(credits.get("cast") or [], key=lambda item: (int(item.get("order", 1_000_000)), int(item.get("id", 1_000_000))))
    cast = unique_ints(cast_items, limit=5)
    keywords = unique_ints(keyword_items(primary))
    text = merge_text(primary, english)
    mask = 0
    mask |= 0 if language else MASK_ORIGINAL_LANGUAGE
    mask |= 0 if release_year else MASK_RELEASE_YEAR
    mask |= 0 if runtime else MASK_RUNTIME
    mask |= 0 if genres else MASK_GENRES
    mask |= 0 if directors else MASK_DIRECTORS
    mask |= 0 if cast else MASK_CAST
    mask |= 0 if keywords else MASK_KEYWORDS
    mask |= 0 if text["overview_fallback"] else MASK_OVERVIEW
    mask |= 0 if text["display_title"] else MASK_TITLE
    structured = {
        "movie_id": int(movie_id),
        "tmdb_id": int(tmdb_id),
        "original_language": language or None,
        "release_year": release_year,
        "runtime_minutes": runtime,
        "genre_ids": genres,
        "director_ids": directors,
        "top5_cast_ids": cast,
        "keyword_ids": keywords,
        "missing_mask": mask,
        "feature_eligible": bool(language and release_year and genres),
    }
    text["feature_eligible"] = bool(text["display_title"] and text["overview_fallback"])
    return structured, text


def build_embedding_input(template: str, prefix: str, text: dict[str, Any]) -> str:
    payload = template.format(
        display_title=text["display_title"],
        overview_fallback=text["overview_fallback"],
        genre_names=", ".join(text["genre_names"]),
        director_names=", ".join(text["director_names"]),
        top5_cast_names=", ".join(text["top5_cast_names"]),
        keyword_names=", ".join(text["keyword_names"]),
    )
    return prefix + payload


def process_movie(row: dict[str, Any], client: TmdbClient) -> dict[str, Any]:
    movie_id = int(row["movie_id"])
    imdb_id = nonblank(row.get("imdb_id"))
    attempted_tmdb = row.get("tmdb_id")
    if attempted_tmdb is None or pd.isna(attempted_tmdb):
        return {"movie_id": movie_id, "imdb_id": imdb_id, "status": "TMDB_NOT_FOUND", "reason": "LINK_TMDB_ID_MISSING", "attempted_tmdb_id": None, "http_status": None}
    attempted_tmdb = int(attempted_tmdb)
    primary_response = client.details(attempted_tmdb, "ko-KR")
    selected_tmdb = attempted_tmdb
    identity_status = "ML_TMDB_VERIFIED"
    reason = "LINKED_DETAILS_VERIFIED"

    if primary_response["status"] in TERMINAL_STATUSES:
        if not imdb_id:
            return {"movie_id": movie_id, "imdb_id": imdb_id, "status": "TMDB_NOT_FOUND", "reason": "TERMINAL_DETAILS_WITHOUT_IMDB", "attempted_tmdb_id": attempted_tmdb, "http_status": primary_response["status"]}
        find_response = client.find_by_imdb(imdb_id)
        if not (200 <= int(find_response["status"]) < 300):
            return {"movie_id": movie_id, "imdb_id": imdb_id, "status": "IDENTITY_REVIEW_REQUIRED", "reason": "IMDB_RECOVERY_HTTP_FAILURE", "attempted_tmdb_id": attempted_tmdb, "http_status": find_response["status"]}
        action, recovered_id, reason = select_recovery(find_response["body"] or {})
        if action != "RECOVER":
            status = {"TV": "TYPE_MISMATCH_TV", "NOT_FOUND": "TMDB_NOT_FOUND"}.get(action, "IDENTITY_REVIEW_REQUIRED")
            return {"movie_id": movie_id, "imdb_id": imdb_id, "status": status, "reason": reason, "attempted_tmdb_id": attempted_tmdb, "http_status": primary_response["status"]}
        selected_tmdb = int(recovered_id)
        primary_response = client.details(selected_tmdb, "ko-KR")
        identity_status = "RECOVERED_BY_IMDB"
        if not (200 <= int(primary_response["status"]) < 300):
            return {"movie_id": movie_id, "imdb_id": imdb_id, "status": "IDENTITY_REVIEW_REQUIRED", "reason": "RECOVERED_DETAILS_HTTP_FAILURE", "attempted_tmdb_id": attempted_tmdb, "http_status": primary_response["status"]}
    elif not (200 <= int(primary_response["status"]) < 300):
        return {"movie_id": movie_id, "imdb_id": imdb_id, "status": "IDENTITY_REVIEW_REQUIRED", "reason": "DETAILS_HTTP_FAILURE", "attempted_tmdb_id": attempted_tmdb, "http_status": primary_response["status"]}

    body = primary_response["body"] or {}
    valid, validation_reason = validate_details_identity(body, selected_tmdb, imdb_id)
    if not valid:
        return {"movie_id": movie_id, "imdb_id": imdb_id, "status": "IDENTITY_REVIEW_REQUIRED", "reason": validation_reason, "attempted_tmdb_id": attempted_tmdb, "http_status": primary_response["status"]}
    english_response = None
    if not nonblank(body.get("title")) or not nonblank(body.get("overview")):
        candidate = client.details(selected_tmdb, "en-US")
        if 200 <= int(candidate["status"]) < 300:
            english_response = candidate
    structured, text = extract_features(movie_id, selected_tmdb, body, english_response["body"] if english_response else None)
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
    }


def encode_texts(
    texts: list[str],
    embedding: dict[str, Any],
    batch_size: int,
    checkpoint_root: Path,
    resume: bool,
) -> np.ndarray:
    try:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from transformers import AutoTokenizer
    except ImportError as error:
        raise RuntimeError("embedding requires onnxruntime and transformers; install requirements-ml.txt") from error
    model_id = embedding["model_id"]
    revision = embedding["model_revision"]
    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision, subfolder="onnx")
    model_path = Path(hf_hub_download(model_id, filename=embedding["model_artifact"], revision=revision))
    if sha256_file(model_path) != embedding["model_artifact_sha256"]:
        raise RuntimeError("pinned ONNX model artifact checksum mismatch")
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    input_names = {item.name for item in session.get_inputs()}
    dimension = int(embedding["dimension"])
    signature_digest = hashlib.sha256()
    signature_digest.update(canonical_json_bytes({
        "model_id": model_id,
        "model_revision": revision,
        "model_artifact_sha256": embedding["model_artifact_sha256"],
        "maximum_tokens": int(embedding["maximum_tokens"]),
        "batch_size": int(batch_size),
        "dimension": dimension,
    }))
    for text in texts:
        signature_digest.update(hashlib.sha256(text.encode("utf-8")).digest())
    signature = signature_digest.hexdigest()
    checkpoint_array = checkpoint_root / "embedding-checkpoint.npy"
    checkpoint_meta = checkpoint_root / "embedding-checkpoint.json"
    start_offset = 0
    expected_shape = (len(texts), dimension)
    if resume and checkpoint_array.is_file() and checkpoint_meta.is_file():
        metadata = json.loads(checkpoint_meta.read_text(encoding="utf-8"))
        if metadata.get("signature") == signature and tuple(metadata.get("shape", [])) == expected_shape:
            result = np.lib.format.open_memmap(checkpoint_array, mode="r+")
            start_offset = int(metadata.get("next_start", 0))
            if not 0 <= start_offset <= len(texts):
                raise RuntimeError("embedding checkpoint offset is invalid")
            print(f"embedding resume: {start_offset:,}/{len(texts):,}", flush=True)
        else:
            result = np.lib.format.open_memmap(checkpoint_array, mode="w+", dtype=np.float32, shape=expected_shape)
    else:
        checkpoint_root.mkdir(parents=True, exist_ok=True)
        result = np.lib.format.open_memmap(checkpoint_array, mode="w+", dtype=np.float32, shape=expected_shape)
    ordered_indices = sorted(range(len(texts)), key=lambda index: (len(texts[index]), index))
    for start in range(start_offset, len(ordered_indices), batch_size):
        batch_indices = ordered_indices[start : start + batch_size]
        tokens = tokenizer([texts[index] for index in batch_indices], max_length=int(embedding["maximum_tokens"]), padding=True, truncation=True, return_tensors="np")
        feed = {name: np.asarray(value, dtype=np.int64) for name, value in tokens.items() if name in input_names}
        if "token_type_ids" in input_names and "token_type_ids" not in feed:
            feed["token_type_ids"] = np.zeros_like(feed["input_ids"], dtype=np.int64)
        output = np.asarray(session.run(None, feed)[0], dtype=np.float32)
        attention = np.asarray(tokens["attention_mask"], dtype=np.float32)[..., None]
        pooled = (output * attention).sum(axis=1) / np.maximum(attention.sum(axis=1), 1e-9)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        pooled = pooled / np.maximum(norms, 1e-12)
        result[batch_indices] = pooled.astype(np.float32)
        result.flush()
        write_json(checkpoint_meta, {
            "schema_version": 1,
            "signature": signature,
            "shape": list(expected_shape),
            "batch_size": int(batch_size),
            "next_start": min(start + batch_size, len(texts)),
            "complete": min(start + batch_size, len(texts)) == len(texts),
        })
        print(f"embeddings: {min(start + batch_size, len(texts)):,}/{len(texts):,}", flush=True)
    if result.shape[1] != int(embedding["dimension"]):
        raise RuntimeError(f"embedding dimension mismatch: {result.shape}")
    return result


def _identity_table(rows: list[dict[str, Any]]) -> pa.Table:
    schema = pa.schema([
        ("movie_id", pa.int32()), ("imdb_id", pa.string()), ("tmdb_id", pa.int32()),
        ("identity_status", pa.string()), ("media_type", pa.string()),
        ("source_updated_at", pa.timestamp("us", tz="UTC")), ("fetched_at", pa.timestamp("us", tz="UTC")),
        ("response_sha256", pa.string()),
    ])
    payload = {
        "movie_id": [row["movie_id"] for row in rows],
        "imdb_id": [row.get("imdb_id", "") for row in rows],
        "tmdb_id": [row.get("tmdb_id") for row in rows],
        "identity_status": [row["status"] for row in rows],
        "media_type": ["tv" if row["status"] == "TYPE_MISMATCH_TV" else ("movie" if row["status"] in IDENTITY_ELIGIBLE else "unknown") for row in rows],
        "source_updated_at": [None] * len(rows),
        "fetched_at": [pd.Timestamp(row.get("fetched_at") or datetime.now(timezone.utc).isoformat()) for row in rows],
        "response_sha256": [row.get("response_sha256") for row in rows],
    }
    return pa.Table.from_pydict(payload, schema=schema)


def _structured_table(rows: list[dict[str, Any]]) -> pa.Table:
    schema = pa.schema([
        ("movie_id", pa.int32()), ("tmdb_id", pa.int32()), ("original_language", pa.string()),
        ("release_year", pa.int16()), ("runtime_minutes", pa.int16()), ("genre_ids", pa.list_(pa.int32())),
        ("director_ids", pa.list_(pa.int32())), ("top5_cast_ids", pa.list_(pa.int32())),
        ("keyword_ids", pa.list_(pa.int32())), ("missing_mask", pa.uint16()), ("feature_eligible", pa.bool_()),
    ])
    return pa.Table.from_pylist(rows, schema=schema)


def _embedding_table(rows: list[dict[str, Any]], vectors: np.ndarray, embedding: dict[str, Any]) -> pa.Table:
    dimension = int(embedding["dimension"])
    schema = pa.schema([
        ("movie_id", pa.int32()), ("model_id", pa.string()), ("model_revision", pa.string()),
        ("input_text_sha256", pa.string()), ("embedding", pa.list_(pa.float32(), dimension)),
        ("l2_norm", pa.float32()), ("feature_eligible", pa.bool_()),
    ])
    payload = []
    for row, vector in zip(rows, vectors):
        payload.append({
            "movie_id": row["movie_id"], "model_id": embedding["model_id"],
            "model_revision": embedding["model_revision"], "input_text_sha256": row["input_text_sha256"],
            "embedding": vector.tolist(), "l2_norm": float(np.linalg.norm(vector)),
            "feature_eligible": bool(row["feature_eligible"]),
        })
    return pa.Table.from_pylist(payload, schema=schema)


def _quarantine_table(rows: list[dict[str, Any]]) -> pa.Table:
    schema = pa.schema([
        ("movie_id", pa.int32()), ("attempted_tmdb_id", pa.int32()), ("identity_status", pa.string()),
        ("reason_code", pa.string()), ("last_http_status", pa.int16()),
    ])
    payload = [{"movie_id": row["movie_id"], "attempted_tmdb_id": row.get("attempted_tmdb_id"), "identity_status": row["status"], "reason_code": row["reason"], "last_http_status": row.get("http_status")} for row in rows]
    return pa.Table.from_pylist(payload, schema=schema)


def build_artifacts(args: argparse.Namespace) -> dict[str, Any]:
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    token = load_env_value(args.env_file, contract["inputs"]["tmdb_auth_env"])
    candidates, scanned = derive_candidate_movies(args.archive, args.chunksize)
    total_candidates = len(candidates)
    linked_count = int(candidates["tmdb_id"].notna().sum())
    if args.preflight:
        candidates = candidates[candidates["tmdb_id"].notna()].copy()
        candidates["sample_digest"] = candidates["movie_id"].map(movie_sample_digest)
        candidates = candidates.sort_values(["sample_digest", "movie_id"]).head(args.limit).drop(columns="sample_digest")
    elif args.limit:
        raise RuntimeError("--limit is allowed only with --preflight")
    output_root = args.output_root or REPO_ROOT / (contract["preflight"]["output_root"] if args.preflight else contract["output_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    cache_root = output_root / "tmdb-cache"
    transport = contract["transport"]
    client = TmdbClient(token, cache_root, resume=args.resume, refresh=args.refresh, maximum_attempts=int(transport["maximum_attempts"]), retry_schedule=tuple(transport["retry_schedule_seconds"]))
    records: list[dict[str, Any]] = []
    started = time.time()
    progress_every = 10 if args.preflight else 500
    with ThreadPoolExecutor(max_workers=min(args.max_concurrency, int(transport["maximum_concurrency"]))) as executor:
        futures = {executor.submit(process_movie, row, client): int(row["movie_id"]) for row in candidates.to_dict("records")}
        for index, future in enumerate(as_completed(futures), start=1):
            records.append(future.result())
            if index % progress_every == 0 or index == len(futures):
                print(f"TMDB: {index:,}/{len(futures):,} movies ({time.time() - started:.1f}s)", flush=True)
            if index % int(transport["checkpoint_every_movies"]) == 0:
                write_json(output_root / "checkpoint.json", {"completed": index, "selected": len(futures), "resume_uses_cache": True})
    records.sort(key=lambda row: row["movie_id"])
    eligible = [row for row in records if row["status"] in IDENTITY_ELIGIBLE]
    quarantine = [row for row in records if row["status"] not in IDENTITY_ELIGIBLE]
    structured_rows = [row["structured"] for row in eligible]
    text_rows: list[dict[str, Any]] = []
    embedding_inputs: list[str] = []
    for row in eligible:
        input_text = build_embedding_input(contract["embedding"]["input_template"], contract["embedding"]["input_prefix"], row["text"])
        embedding_inputs.append(input_text)
        text_rows.append({"movie_id": row["movie_id"], "input_text_sha256": hashlib.sha256(input_text.encode("utf-8")).hexdigest(), "feature_eligible": row["text"]["feature_eligible"]})
    vectors = encode_texts(
        embedding_inputs,
        contract["embedding"],
        args.embedding_batch_size,
        output_root,
        args.resume,
    )

    pq.write_table(_identity_table(records), output_root / "movie-identity.parquet", compression="zstd")
    pq.write_table(_structured_table(structured_rows), output_root / "structured-features.parquet", compression="zstd")
    pq.write_table(_embedding_table(text_rows, vectors, contract["embedding"]), output_root / "text-embeddings.parquet", compression="zstd")
    pq.write_table(_quarantine_table(quarantine), output_root / "quarantine.parquet", compression="zstd")

    selected_linked = sum(row.get("attempted_tmdb_id") is not None for row in records)
    identity_rate = len(eligible) / selected_linked if selected_linked else 0.0
    structured_rate = sum(bool(row["feature_eligible"]) for row in structured_rows) / len(eligible) if eligible else 0.0
    text_rate = sum(bool(row["feature_eligible"]) for row in text_rows) / len(eligible) if eligible else 0.0
    status_counts = pd.Series([row["status"] for row in records]).value_counts().sort_index().to_dict() if records else {}
    summary = {
        "schema_version": 2,
        "evidence_id": "REC-EV-019B-PREFLIGHT" if args.preflight else "REC-EV-019B",
        "scope": "DETERMINISTIC_LINKED_SAMPLE_NOT_FULL_GATE_EVIDENCE" if args.preflight else "FULL_BASE_TRAIN_CANDIDATE_CORE",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_checksums": {"movielens_archive_sha256": sha256_file(args.archive), "contract_sha256": sha256_file(args.contract)},
        "source_rating_rows_scanned": scanned,
        "base_train_candidate_movies": total_candidates,
        "base_train_linked_movies": linked_count,
        "selected_movies": len(records),
        "identity_coverage": {"eligible": len(eligible), "linked_denominator": selected_linked, "rate": identity_rate, "status_counts": status_counts},
        "structured_coverage": {"eligible": sum(bool(row["feature_eligible"]) for row in structured_rows), "denominator": len(eligible), "rate": structured_rate},
        "text_coverage": {"eligible": sum(bool(row["feature_eligible"]) for row in text_rows), "denominator": len(eligible), "rate": text_rate},
        "quarantine_counts": {str(key): int(value) for key, value in status_counts.items() if key not in IDENTITY_ELIGIBLE},
        "forbidden_feature_audit": {"fields": contract["preference_feature_forbidden_fields"], "present_in_output_schema": False, "present_in_embedding_template": False},
        "embedding": {"model_id": contract["embedding"]["model_id"], "model_revision": contract["embedding"]["model_revision"], "dimension": int(contract["embedding"]["dimension"]), "l2_min": float(np.linalg.norm(vectors, axis=1).min()) if len(vectors) else None, "l2_max": float(np.linalg.norm(vectors, axis=1).max()) if len(vectors) else None},
        "cache": {"resume_required": True, "cache_files": len(list(cache_root.glob("*.json"))), "cache_hits_this_run": client.cache_hits, "network_requests_this_run": client.network_requests, "auth_material_written": False},
        "full_gate_claimed": False,
        "product_policy_changed": False,
    }
    gates = contract["gates"]
    content_health_pass = (
        identity_rate >= float(gates["verified_or_recovered_identity_rate_of_linked_min"])
        and structured_rate >= float(gates["structured_feature_eligible_rate_of_identity_eligible_min"])
        and text_rate >= float(gates["text_feature_eligible_rate_of_identity_eligible_min"])
    )
    full_gate_pass = content_health_pass and linked_count / total_candidates >= float(gates["movielens_tmdb_link_present_rate_min"])
    summary["content_health_pass"] = content_health_pass
    summary["full_gate_pass"] = None if args.preflight else full_gate_pass
    summary["full_gate_claimed"] = bool(not args.preflight and full_gate_pass)
    write_json(output_root / "coverage-summary.json", summary)
    artifacts = []
    for path in sorted(output_root.glob("*")):
        if path.is_file() and path.name != "checkpoint.json" and not path.name.startswith("embedding-checkpoint"):
            artifacts.append({"path": path.relative_to(REPO_ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {
        "schema_version": 2,
        "evidence_id": summary["evidence_id"],
        "status": ("PASS_PREFLIGHT" if content_health_pass else "BLOCKED_PREFLIGHT_GATE_FAILURE") if args.preflight else ("PASS_FULL_GATES" if full_gate_pass else "BLOCKED_FULL_GATE_FAILURE"),
        "preflight": bool(args.preflight),
        "model_revision": contract["embedding"]["model_revision"],
        "contract": args.contract.relative_to(REPO_ROOT).as_posix(),
        "contract_sha256": sha256_file(args.contract),
        "source_checksums": summary["source_checksums"],
        "artifacts": artifacts,
        "validation": {"selected_movies": len(records), "identity_rate": identity_rate, "structured_rate": structured_rate, "text_rate": text_rate, "locked_test_opened": False, "product_policy_changed": False},
    }
    manifest_path = args.manifest or REPO_ROOT / (contract["preflight"]["manifest"] if args.preflight else "docs/recommendation/evidence/manifests/rec-ev-019b.json")
    write_json(manifest_path, manifest)
    return {"manifest": str(manifest_path), "status": manifest["status"], "summary": summary}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build REC-EV-019B TMDB content artifacts")
    parser.add_argument("--contract", type=Path, default=REPO_ROOT / "docs/recommendation/contracts/rec-ev-019b-artifacts.json")
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env.local")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--max-concurrency", type=int, default=8)
    parser.add_argument("--embedding-batch-size", type=int, default=32)
    parser.add_argument("--chunksize", type=int, default=1_000_000)
    args = parser.parse_args()
    args.contract = args.contract.resolve()
    args.archive = args.archive.resolve()
    args.env_file = args.env_file.resolve()
    if args.output_root:
        args.output_root = args.output_root.resolve()
    if args.manifest:
        args.manifest = args.manifest.resolve()
    if args.preflight and args.limit is None:
        args.limit = 100
    if args.preflight and args.limit <= 0:
        parser.error("--preflight requires a positive --limit")
    return args


def main() -> int:
    try:
        result = build_artifacts(parse_args())
        print(json.dumps({"status": result["status"], "manifest": result["manifest"]}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(f"REC-EV-019B build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
