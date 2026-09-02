"""Atomic artifact writers for the real REC-EV-019C Validation run."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from rec_ev_019c_data import PreparedInputs
from run_rec_ev_019c_validation import atomic_write_json, canonical_json_bytes, expand_trials, sha256_file


PREDICTION_SCHEMA = pa.schema([
    ("user_key", pa.string()), ("k", pa.int8()), ("model_id", pa.string()),
    ("trial_id", pa.string()), ("rank", pa.int16()), ("movie_id", pa.int32()),
    ("effective_score", pa.float32()), ("fallback_used", pa.bool_()),
    ("fallback_reason", pa.string()),
])


class PredictionParquetSink:
    def __init__(self, path: Path, *, flush_rows: int = 32000) -> None:
        self.path = path
        self.temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.writer = pq.ParquetWriter(self.temporary, PREDICTION_SCHEMA, compression="zstd")
        self.buffer: list[dict[str, Any]] = []
        self.flush_rows = int(flush_rows)
        self.row_count = 0

    def __call__(
        self,
        model_id: str,
        k: int,
        trial_id: str,
        user_key: str,
        rows: Sequence[Mapping[str, Any]],
    ) -> None:
        self.buffer.extend({
            "user_key": str(user_key), "k": int(k), "model_id": str(model_id),
            "trial_id": str(trial_id), "rank": int(row["rank"]), "movie_id": int(row["movie_id"]),
            "effective_score": float(row["effective_score"]),
            "fallback_used": bool(row["fallback_used"]),
            "fallback_reason": row.get("fallback_reason"),
        } for row in rows)
        if len(self.buffer) >= self.flush_rows:
            self.flush()

    def flush(self) -> None:
        if not self.buffer:
            return
        table = pa.Table.from_pylist(self.buffer, schema=PREDICTION_SCHEMA)
        self.writer.write_table(table)
        self.row_count += len(self.buffer)
        self.buffer.clear()

    def close(self) -> None:
        self.flush()
        self.writer.close()
        os.replace(self.temporary, self.path)

    def abort(self) -> None:
        self.writer.close()


class RunProgress:
    def __init__(self, path: Path, resume_signature: str) -> None:
        self.path = path
        self.resume_signature = resume_signature
        self.events: list[dict[str, Any]] = []
        self.peak_rss_bytes = 0
        self.started_at = time.monotonic()

    def __call__(self, event: Mapping[str, Any]) -> None:
        try:
            import psutil
            self.peak_rss_bytes = max(self.peak_rss_bytes, int(psutil.Process().memory_info().rss))
        except Exception:
            pass
        row = {**event, "sequence": len(self.events) + 1}
        self.events.append(row)
        atomic_write_json(self.path, {
            "resume_signature": self.resume_signature,
            "completed_events": self.events,
            "peak_rss_bytes": self.peak_rss_bytes,
            "wall_clock_seconds": time.monotonic() - self.started_at,
        })


def resume_signature(contract_path: Path, input_checksums: Mapping[str, str]) -> str:
    payload = canonical_json_bytes({
        "contract_sha256": sha256_file(contract_path),
        "input_checksums": dict(sorted(input_checksums.items())),
    })
    return hashlib.sha256(payload).hexdigest()


def write_candidate_core(
    path: Path,
    inputs: PreparedInputs,
    *,
    b0_score: np.ndarray,
) -> None:
    frame = inputs.candidate_core[["movie_id", "tmdb_id", "identity_status"]].copy()
    frame["movie_id"] = frame["movie_id"].astype("int32")
    frame["tmdb_id"] = frame["tmdb_id"].astype("int32")
    frame["b0_rating_count"] = inputs.b0_rating_count.astype("int64")
    frame["b0_rating_mean"] = inputs.b0_rating_mean.astype("float32")
    frame["b0_score"] = np.asarray(b0_score, dtype=np.float32)
    frame["structured_available"] = inputs.structured_available.astype(bool)
    frame["text_available"] = inputs.text_available.astype(bool)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, path)


def _write_frame(path: Path, frame: pd.DataFrame, columns: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.loc[:, list(columns)].to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, path)


def write_trial_metrics(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    columns = [
        "user_key", "k", "model_id", "trial_id", "seed", "evaluation_phase",
        "ndcg_at_10", "recall_at_10", "mrr_at_10", "positive_mean_rank_percentile",
        "candidate_recall_at_500", "fallback_user",
    ]
    frame = pd.DataFrame(rows)
    frame = frame.sort_values(
        ["evaluation_phase", "model_id", "trial_id", "seed", "k", "user_key"],
        kind="stable", na_position="first", ignore_index=True,
    )
    _write_frame(path, frame, columns)


def write_validation_metrics(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    columns = [
        "user_key", "k", "model_id", "ndcg_at_10", "recall_at_10", "mrr_at_10",
        "positive_mean_rank_percentile", "candidate_recall_at_500", "harm_at_2",
        "miss_at_2", "both_good_at_2", "safe_hit_at_2", "fallback_user",
    ]
    frame = pd.DataFrame(rows).sort_values(["model_id", "k", "user_key"], kind="stable", ignore_index=True)
    _write_frame(path, frame, columns)


def write_registry(
    path: Path,
    contract: Mapping[str, Any],
    *,
    contract_sha256: str,
    input_checksums: Mapping[str, str],
    signature: str,
) -> None:
    atomic_write_json(path, {
        "contract_sha256": contract_sha256,
        "input_checksums": dict(sorted(input_checksums.items())),
        "model_order": list(contract["trial_execution"]["model_order"]),
        "trials": expand_trials(contract),
        "resume_signature": signature,
        "locked_test_opened": False,
    })


def artifact_entry(path: Path, *, root: Path) -> dict[str, Any]:
    return {
        "path": path.resolve().relative_to(root.resolve()).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
