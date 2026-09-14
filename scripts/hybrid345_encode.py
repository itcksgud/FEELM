"""Pinned, label-free Qwen3 document encoding for hybrid345.

The network is used only by the explicit ``download`` action.  Token audit,
pilot, and full encoding load the pinned local directory with
``local_files_only=True``.  Full encoding writes a catalogue-aligned float32
NumPy matrix to a staging memmap and publishes it atomically.  A partial run is
never resumed because changing batch composition measurably changes BF16 output.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from hybrid345_common import (DOC, OUT, ROOT, RUNTIME, load_config, pin, read_json, require,
                              require_prelabel_review, write_json)
from hybrid345_documents import verify_documents_seal


MODEL_DIR = RUNTIME / "model"
MODEL_SEAL = RUNTIME / "model-seal.json"
DOCUMENTS = OUT / "documents.parquet"
DOCUMENT_SEAL = OUT / "documents-seal.json"
TOKEN_LENGTHS = OUT / "qwen-token-lengths.npy"
TOKEN_AUDIT = OUT / "qwen-token-audit.json"
TOKEN_AUDIT_SEAL = OUT / "qwen-token-audit-seal.json"
PILOT_IDS = OUT / "qwen-pilot-movie-ids.npy"
PILOT_EMBEDDINGS = OUT / "qwen-pilot-embeddings.npy"
PILOT_REPORT = OUT / "qwen-pilot.json"
PILOT_SEAL = OUT / "qwen-pilot-seal.json"
PILOT_REVIEW = DOC / "pilot-review.json"
FINAL_IDS = OUT / "qwen-movie-ids.npy"
FINAL_EMBEDDINGS = OUT / "qwen-embeddings.npy"
FINAL_SUMMARY = OUT / "qwen-embedding-summary.json"
FINAL_SEAL = OUT / "qwen-embedding-seal.json"
PARTIAL_EMBEDDINGS = RUNTIME / "qwen-full.partial.npy"
FULL_PROGRESS = RUNTIME / "qwen-full-progress.json"
PILOT_SIZE = 504
PILOT_SALT = "hybrid345-qwen-exact-pilot-v1|"
SEED = 345
BATCH_SIZE = 8


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _atomic_save_npy(path: Path, value: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    require(not temporary.exists(), f"stale NumPy staging file: {temporary}")
    try:
        with temporary.open("wb") as handle:
            np.save(handle, value, allow_pickle=False)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _package_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _implementation_pins() -> dict[str, dict[str, Any]]:
    return {
        "scripts/hybrid345_common.py": pin(ROOT / "scripts/hybrid345_common.py"),
        "scripts/hybrid345_encode.py": pin(Path(__file__)),
        "docs/recommendation/experiments/hybrid345/config.json": pin(DOC / "config.json"),
    }


def _model_inventory(model_dir: Path = MODEL_DIR) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(model_dir.rglob("*")):
        if not path.is_file() or ".cache" in path.relative_to(model_dir).parts:
            continue
        rows.append({"path": path.relative_to(model_dir).as_posix(), **pin(path)})
    return rows


def _download_metadata_revision(model_dir: Path = MODEL_DIR) -> str | None:
    metadata = model_dir / ".cache" / "huggingface" / "download" / "config.json.metadata"
    if not metadata.is_file():
        return None
    lines = metadata.read_text(encoding="utf-8").splitlines()
    return lines[0].strip() if lines else None


def _validate_local_model(cfg: dict[str, Any], model_dir: Path = MODEL_DIR) -> list[dict[str, Any]]:
    require(model_dir.is_dir(), f"local Qwen model directory is missing: {model_dir}")
    required = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json")
    for name in required:
        require((model_dir / name).is_file(), f"local Qwen model file missing: {name}")
    revision = _download_metadata_revision(model_dir)
    require(revision == cfg["qwen"]["revision"],
            f"downloaded Qwen revision mismatch: {revision!r}")
    model_config = read_json(model_dir / "config.json")
    require(int(model_config.get("hidden_size", -1)) == int(cfg["qwen"]["dimension"]),
            "Qwen hidden size does not match the fixed output dimension")
    inventory = _model_inventory(model_dir)
    require(inventory, "local Qwen model inventory is empty")
    feasibility_path = ROOT / cfg["qwen"]["pilot_artifacts"]["benchmark"]
    relative = _relative(feasibility_path)
    require(pin(feasibility_path) == cfg["source_pins"][relative],
            "pinned feasibility pilot benchmark drift")
    expected_inventory = read_json(feasibility_path).get("model_files")
    require(inventory == expected_inventory, "local Qwen model files differ from the pinned pilot")
    return inventory


def prepare_model() -> dict[str, Any]:
    """Download the exact revision once, or seal an already downloaded snapshot."""
    require_prelabel_review()
    cfg = load_config()
    qwen = cfg["qwen"]
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if MODEL_SEAL.exists():
        return verify_model_seal()
    if not MODEL_DIR.exists():
        try:
            from huggingface_hub import snapshot_download
        except ImportError as error:
            raise RuntimeError("download requires huggingface_hub") from error
        staging = RUNTIME / "model.download.partial"
        staging.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=str(qwen["model_id"]),
            revision=str(qwen["revision"]),
            local_dir=staging,
        )
        os.replace(staging, MODEL_DIR)
    inventory = _validate_local_model(cfg)
    seal = {
        "schema_version": 1,
        "status": "PASS_PINNED_LOCAL_MODEL",
        "model_id": qwen["model_id"],
        "revision": qwen["revision"],
        "local_path": _relative(MODEL_DIR),
        "files": inventory,
        "total_bytes": int(sum(row["bytes"] for row in inventory)),
        "network_allowed_after_this_stage": False,
    }
    write_json(MODEL_SEAL, seal)
    print("PASS_PINNED_LOCAL_MODEL", json.dumps({"files": len(inventory)}), flush=True)
    return seal


def verify_model_seal() -> dict[str, Any]:
    cfg = load_config()
    seal = read_json(MODEL_SEAL)
    require(seal.get("status") == "PASS_PINNED_LOCAL_MODEL", "Qwen model seal status")
    require(seal.get("model_id") == cfg["qwen"]["model_id"], "Qwen model ID drift")
    require(seal.get("revision") == cfg["qwen"]["revision"], "Qwen model revision drift")
    expected = seal.get("files")
    actual = _model_inventory()
    require(actual == expected, "pinned local Qwen model files changed")
    require(_download_metadata_revision() == cfg["qwen"]["revision"], "local revision metadata drift")
    return seal


def verify_document_seal() -> dict[str, Any]:
    return verify_documents_seal()


def _load_documents() -> tuple[np.ndarray, list[str]]:
    verify_document_seal()
    frame = pd.read_parquet(DOCUMENTS, columns=["movie_id", "body"])
    ids = frame["movie_id"].to_numpy(dtype=np.int64, copy=True)
    require(len(ids) == int(load_config()["catalog_movies"]), "Qwen document count drift")
    require(np.array_equal(ids, np.sort(np.unique(ids))), "Qwen document movie axis is not sorted unique")
    bodies = frame["body"].tolist()
    require(all(isinstance(value, str) and value for value in bodies), "empty Qwen document body")
    require(all(not value.startswith("passage: ") for value in bodies), "E5 prefix leaked into Qwen body")
    return ids, bodies


def _load_tokenizer() -> Any:
    verify_model_seal()
    try:
        from transformers import AutoTokenizer
    except ImportError as error:
        raise RuntimeError("Qwen encoding requires transformers") from error
    tokenizer = AutoTokenizer.from_pretrained(
        MODEL_DIR,
        local_files_only=True,
        trust_remote_code=False,
        padding_side="left",
    )
    require(tokenizer.padding_side == "left", "Qwen tokenizer must use left padding")
    return tokenizer


def token_statistics(lengths: np.ndarray) -> dict[str, Any]:
    require(lengths.ndim == 1 and len(lengths) > 0, "empty token length audit")
    quantiles = np.percentile(lengths, [0, 50, 90, 95, 99, 100])
    return {
        "count": int(len(lengths)),
        "min": int(quantiles[0]),
        "mean": float(np.mean(lengths)),
        "p50": float(quantiles[1]),
        "p90": float(quantiles[2]),
        "p95": float(quantiles[3]),
        "p99": float(quantiles[4]),
        "max": int(quantiles[5]),
    }


def audit_tokens(*, tokenizer_batch_size: int = 256) -> dict[str, Any]:
    require(tokenizer_batch_size > 0, "tokenizer batch size must be positive")
    require_prelabel_review()
    if TOKEN_AUDIT_SEAL.exists():
        return verify_token_audit()[0]
    require(not TOKEN_LENGTHS.exists() and not TOKEN_AUDIT.exists(), "unsealed token audit exists")
    cfg = load_config()
    ids, bodies = _load_documents()
    tokenizer = _load_tokenizer()
    lengths = np.empty(len(bodies), dtype=np.int32)
    started = time.perf_counter()
    for start in range(0, len(bodies), tokenizer_batch_size):
        block = tokenizer(
            bodies[start : start + tokenizer_batch_size],
            padding=False,
            truncation=False,
            add_special_tokens=True,
        )["input_ids"]
        lengths[start : start + len(block)] = [len(row) for row in block]
        if start % (tokenizer_batch_size * 20) == 0:
            print(f"TOKENS {min(start + tokenizer_batch_size, len(bodies))}/{len(bodies)}", flush=True)
    maximum = int(cfg["qwen"]["max_length"])
    require((lengths > 0).all(), "Qwen tokenizer produced an empty sequence")
    require(int(lengths.max()) <= maximum,
            f"Qwen input would be truncated: max={int(lengths.max())}, limit={maximum}")
    _atomic_save_npy(TOKEN_LENGTHS, lengths)
    report = {
        "schema_version": 1,
        "status": "PASS_ZERO_TRUNCATION",
        "movies": len(ids),
        "max_length": maximum,
        "documents_over_limit": int((lengths > maximum).sum()),
        "truncation_enabled": False,
        "token_statistics": token_statistics(lengths),
        "seconds": time.perf_counter() - started,
        "padding_side_for_encoding": "left",
        "ratings_opened": False,
        "labels_opened": False,
        "network_requests": 0,
    }
    write_json(TOKEN_AUDIT, report)
    seal = {
        "schema_version": 1,
        "status": "PASS_ZERO_TRUNCATION",
        "parents": {"documents-seal.json": pin(DOCUMENT_SEAL), "model-seal.json": pin(MODEL_SEAL)},
        "artifacts": {
            "qwen-token-lengths.npy": pin(TOKEN_LENGTHS),
            "qwen-token-audit.json": pin(TOKEN_AUDIT),
        },
        "implementation": _implementation_pins(),
    }
    write_json(TOKEN_AUDIT_SEAL, seal)
    print("PASS_ZERO_TRUNCATION", json.dumps(report["token_statistics"]), flush=True)
    return seal


def verify_token_audit() -> tuple[dict[str, Any], np.ndarray]:
    seal = read_json(TOKEN_AUDIT_SEAL)
    require(seal.get("status") == "PASS_ZERO_TRUNCATION", "token audit seal status")
    verify_document_seal()
    verify_model_seal()
    require(seal["parents"]["documents-seal.json"] == pin(DOCUMENT_SEAL), "token audit document parent drift")
    require(seal["parents"]["model-seal.json"] == pin(MODEL_SEAL), "token audit model parent drift")
    require(seal.get("implementation") == _implementation_pins(), "token audit implementation drift")
    for name, expected in seal["artifacts"].items():
        require(pin(OUT / name) == expected, f"token audit artifact drift: {name}")
    lengths = np.load(TOKEN_LENGTHS, allow_pickle=False)
    require(lengths.shape == (int(load_config()["catalog_movies"]),) and lengths.dtype == np.int32,
            "token length vector shape/dtype drift")
    maximum = int(load_config()["qwen"]["max_length"])
    require((lengths > 0).all() and int(lengths.max()) <= maximum, "token audit no longer proves zero truncation")
    return seal, lengths


def last_token_pool(last_hidden_states: Any, attention_mask: Any) -> Any:
    """Pool the final non-padding token for either left- or right-padded tensors."""
    import torch

    require(last_hidden_states.ndim == 3 and attention_mask.ndim == 2,
            "last-token pooling tensor rank")
    require(last_hidden_states.shape[:2] == attention_mask.shape, "last-token pooling shape")
    require(bool((attention_mask.sum(dim=1) > 0).all().item()), "empty token sequence")
    left_padded = bool((attention_mask[:, -1] == 1).all().item())
    if left_padded:
        return last_hidden_states[:, -1]
    lengths = attention_mask.sum(dim=1).to(dtype=torch.long) - 1
    rows = torch.arange(last_hidden_states.shape[0], device=last_hidden_states.device)
    return last_hidden_states[rows, lengths]


def make_consecutive_batches(
    indices: Sequence[int] | np.ndarray, *, batch_size: int = BATCH_SIZE
) -> list[np.ndarray]:
    """Keep canonical input order and divide it into consecutive fixed-size batches."""
    require(batch_size > 0, "invalid batch size")
    indices = np.asarray(indices, dtype=np.int64)
    require(indices.ndim == 1 and len(np.unique(indices)) == len(indices), "invalid batch indices")
    require(np.array_equal(indices, np.sort(indices)), "batch indices must be in canonical order")
    batches = [indices[start : start + batch_size] for start in range(0, len(indices), batch_size)]
    require(sum(len(batch) for batch in batches) == len(indices), "consecutive batching lost rows")
    return batches


def pilot_indices(movie_ids: np.ndarray, size: int = PILOT_SIZE) -> np.ndarray:
    """Select whole full-run batch blocks so pilot co-batching is identical."""
    require(0 < size <= len(movie_ids) and size % BATCH_SIZE == 0, "invalid full-batch pilot size")
    full_blocks = len(movie_ids) // BATCH_SIZE
    wanted = size // BATCH_SIZE
    require(wanted <= full_blocks, "not enough complete catalogue batches")
    ordered = sorted(
        range(full_blocks),
        key=lambda block: (
            sha256_text(PILOT_SALT + str(int(movie_ids[block * BATCH_SIZE]))),
            int(movie_ids[block * BATCH_SIZE]),
        ),
    )[:wanted]
    positions = np.concatenate([
        np.arange(block * BATCH_SIZE, (block + 1) * BATCH_SIZE, dtype=np.int64)
        for block in sorted(ordered)
    ])
    require(len(positions) == size, "whole-batch pilot selection")
    return positions


def validate_embeddings(values: np.ndarray, *, rows: int, dimension: int) -> dict[str, Any]:
    require(values.shape == (rows, dimension), f"embedding shape drift: {values.shape}")
    require(values.dtype == np.float32, f"embedding dtype drift: {values.dtype}")
    require(np.isfinite(values).all(), "non-finite Qwen embedding")
    norms = np.linalg.norm(values, axis=1)
    error = float(np.max(np.abs(norms - 1.0))) if len(norms) else 0.0
    require(error <= 1e-5, f"Qwen L2 normalization drift: {error}")
    return {
        "shape": list(values.shape),
        "dtype": str(values.dtype),
        "l2_min": float(norms.min()) if len(norms) else None,
        "l2_max": float(norms.max()) if len(norms) else None,
        "l2_max_abs_error": error,
        "all_finite": True,
    }


def _configure_torch() -> Any:
    try:
        import torch
    except ImportError as error:
        raise RuntimeError("Qwen encoding requires PyTorch") from error
    require(torch.cuda.is_available(), "hybrid345 Qwen encoding requires CUDA")
    require(torch.cuda.is_bf16_supported(), "hybrid345 Qwen encoding requires CUDA BF16")
    require(".".join(sys.version.split()[0].split(".")[:2]) == "3.12", "Qwen Python runtime drift")
    require(torch.__version__ == "2.10.0+cu128", "Qwen PyTorch runtime drift")
    require(torch.version.cuda == "12.8", "Qwen CUDA runtime drift")
    require(_package_version("transformers") == "5.16.1", "Qwen Transformers runtime drift")
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.use_deterministic_algorithms(True)
    return torch


def _load_model() -> tuple[Any, Any, Any]:
    torch = _configure_torch()
    tokenizer = _load_tokenizer()
    from transformers import AutoModel

    model = AutoModel.from_pretrained(
        MODEL_DIR,
        local_files_only=True,
        trust_remote_code=False,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    ).to("cuda")
    model.eval()
    model.config.use_cache = False
    cfg = load_config()
    require(int(model.config.hidden_size) == int(cfg["qwen"]["dimension"]), "loaded Qwen dimension drift")
    return torch, tokenizer, model


def _rss_bytes() -> int:
    try:
        import psutil

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except ImportError:
        return 0


def _encode_once(
    model: Any,
    tokenizer: Any,
    torch: Any,
    texts: Sequence[str],
    *,
    expected_lengths: np.ndarray,
    max_length: int,
) -> tuple[np.ndarray, int]:
    batch = tokenizer(
        list(texts),
        padding=True,
        truncation=False,
        add_special_tokens=True,
        return_tensors="pt",
    )
    attention = batch["attention_mask"]
    observed = attention.sum(dim=1).cpu().numpy().astype(np.int32, copy=False)
    require(np.array_equal(observed, np.asarray(expected_lengths, dtype=np.int32)),
            "token lengths changed between audit and encoding")
    require(int(attention.shape[1]) <= max_length, "Qwen batch would require truncation")
    padded_tokens = int(attention.shape[0] * attention.shape[1])
    batch = {name: value.to("cuda", non_blocking=False) for name, value in batch.items()}
    with torch.inference_mode():
        output = model(**batch, use_cache=False)
        pooled = last_token_pool(output.last_hidden_state, batch["attention_mask"])
        values = torch.nn.functional.normalize(pooled.float(), p=2, dim=1)
    result = values.cpu().numpy().astype(np.float32, copy=False)
    return result, padded_tokens


def _encode_exact_batch(
    model: Any,
    tokenizer: Any,
    torch: Any,
    bodies: Sequence[str],
    batch_indices: np.ndarray,
    token_lengths: np.ndarray,
    *,
    max_length: int,
) -> tuple[np.ndarray, int]:
    try:
        values, padded = _encode_once(
            model,
            tokenizer,
            torch,
            [bodies[int(index)] for index in batch_indices],
            expected_lengths=token_lengths[batch_indices],
            max_length=max_length,
        )
        return values, padded
    except torch.cuda.OutOfMemoryError as error:
        torch.cuda.empty_cache()
        raise RuntimeError(
            "RESOURCE_EXCEPTION_CUDA_OOM: batch_size=8 is frozen; do not mix a smaller batch "
            "into this artifact. A different batch size requires a reviewed clean full restart."
        ) from error


def _encode_in_memory(
    ids: np.ndarray,
    bodies: Sequence[str],
    lengths: np.ndarray,
    selected: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    cfg = load_config()
    torch, tokenizer, model = _load_model()
    require(int(cfg["qwen"]["batch_size"]) == BATCH_SIZE, "Qwen batch-size contract drift")
    batches = make_consecutive_batches(selected)
    position = {int(index): offset for offset, index in enumerate(selected)}
    output = np.empty((len(selected), int(cfg["qwen"]["dimension"])), dtype=np.float32)
    padded_tokens = 0
    peak_rss = _rss_bytes()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    started = time.perf_counter()
    for batch_indices in batches:
        values, padded = _encode_exact_batch(
            model, tokenizer, torch, bodies, batch_indices, lengths,
            max_length=int(cfg["qwen"]["max_length"]),
        )
        output[[position[int(index)] for index in batch_indices]] = values
        padded_tokens += padded
        peak_rss = max(peak_rss, _rss_bytes())
    torch.cuda.synchronize()
    seconds = time.perf_counter() - started
    del model
    gc.collect()
    torch.cuda.empty_cache()
    return output, {
        "seconds": seconds,
        "batches": len(batches),
        "padded_tokens": padded_tokens,
        "true_tokens": int(lengths[selected].sum()),
        "gpu_peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
        "gpu_peak_reserved_bytes": int(torch.cuda.max_memory_reserved()),
        "process_peak_rss_bytes": peak_rss,
    }


def run_pilot() -> dict[str, Any]:
    require_prelabel_review()
    if PILOT_SEAL.exists():
        return verify_pilot()
    for path in (PILOT_IDS, PILOT_EMBEDDINGS, PILOT_REPORT):
        require(not path.exists(), f"unsealed pilot artifact exists: {path.name}")
    cfg = load_config()
    require(int(cfg["qwen"].get("pilot_movies", -1)) == PILOT_SIZE,
            "Qwen pilot population contract drift")
    require(cfg["qwen"].get("pilot_selection") ==
            "63_WHOLE_FULL_RUN_BATCH8_BLOCKS_HASHED_BY_FIRST_MOVIE_THEN_RESTORED_BLOCK_ORDER",
            "Qwen pilot selection contract drift")
    _token_seal, lengths = verify_token_audit()
    ids, bodies = _load_documents()
    selected = pilot_indices(ids)
    first, first_resources = _encode_in_memory(ids, bodies, lengths, selected)
    second, second_resources = _encode_in_memory(ids, bodies, lengths, selected)
    validation = validate_embeddings(first, rows=PILOT_SIZE, dimension=int(cfg["qwen"]["dimension"]))
    repeat_difference = float(np.max(np.abs(first - second)))
    repeat_cosine = np.sum(first * second, axis=1)
    require(np.array_equal(first, second), f"same-plan Qwen pilot is not deterministic: {repeat_difference}")
    gpu_limit = int(cfg["resource_limits"]["qwen_gpu_bytes"])
    host_limit = int(cfg["resource_limits"]["host_memory_bytes"])
    require(max(first_resources["gpu_peak_allocated_bytes"], second_resources["gpu_peak_allocated_bytes"]) <= gpu_limit,
            "Qwen pilot exceeded GPU memory limit")
    if first_resources["process_peak_rss_bytes"] and second_resources["process_peak_rss_bytes"]:
        require(max(first_resources["process_peak_rss_bytes"], second_resources["process_peak_rss_bytes"]) <= host_limit,
                "Qwen pilot exceeded host memory limit")
    _atomic_save_npy(PILOT_IDS, ids[selected])
    _atomic_save_npy(PILOT_EMBEDDINGS, first)
    report = {
        "schema_version": 1,
        "status": "PASS",
        "movies": PILOT_SIZE,
        "selection_salt": PILOT_SALT,
        "batch_size": BATCH_SIZE,
        "batching": "WHOLE_FULL_RUN_BATCH_BLOCKS_NO_LENGTH_SORT",
        "full_run_batch_blocks": PILOT_SIZE // BATCH_SIZE,
        "dynamic_padding_within_batch": True,
        "validation": validation,
        "same_plan_repeat": {
            "exact_array_equal": True,
            "max_absolute_difference": repeat_difference,
            "minimum_pair_cosine": float(repeat_cosine.min()),
        },
        "passes": [first_resources, second_resources],
        "ratings_opened": False,
        "labels_opened": False,
        "network_requests": 0,
    }
    write_json(PILOT_REPORT, report)
    seal = {
        "schema_version": 1,
        "status": "PASS",
        "parents": {
            "documents-seal.json": pin(DOCUMENT_SEAL),
            "model-seal.json": pin(MODEL_SEAL),
            "qwen-token-audit-seal.json": pin(TOKEN_AUDIT_SEAL),
        },
        "artifacts": {
            PILOT_IDS.name: pin(PILOT_IDS),
            PILOT_EMBEDDINGS.name: pin(PILOT_EMBEDDINGS),
            PILOT_REPORT.name: pin(PILOT_REPORT),
        },
        "implementation": _implementation_pins(),
    }
    write_json(PILOT_SEAL, seal)
    print("QWEN_PILOT_PASS", json.dumps({"movies": PILOT_SIZE}), flush=True)
    return seal


def verify_pilot() -> dict[str, Any]:
    seal = read_json(PILOT_SEAL)
    require(seal.get("status") == "PASS", "Qwen pilot seal status")
    verify_document_seal()
    verify_model_seal()
    verify_token_audit()
    require(seal.get("parents", {}).get("documents-seal.json") == pin(DOCUMENT_SEAL),
            "pilot document parent drift")
    require(seal.get("parents", {}).get("model-seal.json") == pin(MODEL_SEAL),
            "pilot model parent drift")
    require(seal.get("parents", {}).get("qwen-token-audit-seal.json") == pin(TOKEN_AUDIT_SEAL),
            "pilot token-audit parent drift")
    require(seal.get("implementation") == _implementation_pins(), "pilot implementation drift")
    for name, expected in seal.get("artifacts", {}).items():
        require(pin(OUT / name) == expected, f"pilot artifact drift: {name}")
    report = read_json(PILOT_REPORT)
    cfg = load_config()
    require(int(cfg["qwen"].get("pilot_movies", -1)) == PILOT_SIZE,
            "Qwen pilot population config drift")
    require(report.get("movies") == PILOT_SIZE and report.get("batch_size") == BATCH_SIZE,
            "pilot population/batch drift")
    require(report.get("batching") == "WHOLE_FULL_RUN_BATCH_BLOCKS_NO_LENGTH_SORT",
            "pilot co-batching contract drift")
    require(report.get("same_plan_repeat", {}).get("exact_array_equal") is True,
            "pilot repeat determinism drift")
    return seal


def verify_pilot_review() -> dict[str, Any]:
    """Require an independent review of the exact 504-movie pilot before full encoding."""
    require(PILOT_REVIEW.is_file(), "independent Qwen pilot review is required before full encoding")
    review = read_json(PILOT_REVIEW)
    require(review.get("status") == "PASS", "independent Qwen pilot review did not pass")
    require(review.get("pilot_seal") == pin(PILOT_SEAL), "Qwen pilot review parent drift")
    require(review.get("implementation") == _implementation_pins(),
            "Qwen pilot review implementation drift")
    return review


def _full_signature() -> dict[str, Any]:
    return {
        "config": load_config()["qwen"],
        "documents": pin(DOCUMENTS),
        "document_seal": pin(DOCUMENT_SEAL),
        "model_seal": pin(MODEL_SEAL),
        "token_audit_seal": pin(TOKEN_AUDIT_SEAL),
        "pilot_seal": pin(PILOT_SEAL),
        "pilot_review": pin(PILOT_REVIEW),
        "implementation": _implementation_pins(),
        "batch_size": BATCH_SIZE,
        "batching": "CONSECUTIVE_CANONICAL_MOVIE_ID_NO_LENGTH_SORT",
        "partial_resume": False,
    }


def _runtime_metadata(torch: Any) -> dict[str, Any]:
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "transformers": _package_version("transformers"),
        "huggingface_hub": _package_version("huggingface-hub"),
        "tokenizers": _package_version("tokenizers"),
        "device": torch.cuda.get_device_name(0),
        "device_total_memory_bytes": int(torch.cuda.get_device_properties(0).total_memory),
        "dtype": "bfloat16",
        "attention_implementation": "sdpa",
        "padding_side": "left",
        "pooling": "last_token",
        "normalization": "L2_float32_full_1024",
        "deterministic_algorithms": True,
        "seed": SEED,
    }


def run_full() -> dict[str, Any]:
    require_prelabel_review()
    if FINAL_SEAL.exists():
        return verify_full()
    for path in (FINAL_IDS, FINAL_EMBEDDINGS, FINAL_SUMMARY):
        require(not path.exists(), f"unsealed full Qwen artifact exists: {path.name}")
    cfg = load_config()
    pilot = verify_pilot()
    pilot_review = verify_pilot_review()
    _token_seal, lengths = verify_token_audit()
    ids, bodies = _load_documents()
    dimension = int(cfg["qwen"]["dimension"])
    require(cfg["qwen"].get("batch_size") == BATCH_SIZE, "Qwen batch-size contract drift")
    require(cfg["qwen"].get("batching") == "CONSECUTIVE_NO_LENGTH_SORT_DYNAMIC_PADDING",
            "Qwen batching contract drift")
    require(cfg["qwen"].get("partial_resume") is False, "partial resume must remain disabled")
    signature = _full_signature()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    expected_shape = (len(ids), dimension)
    require(not FULL_PROGRESS.exists() and not PARTIAL_EMBEDDINGS.exists(),
            "partial Qwen run is preserved but cannot be resumed; reviewed clean restart is required")
    values = np.lib.format.open_memmap(
        PARTIAL_EMBEDDINGS, mode="w+", dtype=np.float32, shape=expected_shape
    )
    state = {
        "schema_version": 1,
        "status": "IN_PROGRESS_NO_PARTIAL_RESUME",
        "signature": signature,
        "completed": 0,
        "seconds": 0.0,
        "batches_completed": 0,
        "padded_tokens": 0,
        "true_tokens": 0,
        "gpu_peak_allocated_bytes": 0,
        "gpu_peak_reserved_bytes": 0,
        "process_peak_rss_bytes": _rss_bytes(),
    }
    write_json(FULL_PROGRESS, state)
    batches = make_consecutive_batches(np.arange(len(ids), dtype=np.int64))

    torch, tokenizer, model = _load_model()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    started = time.perf_counter()
    gpu_limit = int(cfg["resource_limits"]["qwen_gpu_bytes"])
    host_limit = int(cfg["resource_limits"]["host_memory_bytes"])
    seconds_limit = int(cfg["resource_limits"]["qwen_seconds"])
    peak_rss = max(int(state.get("process_peak_rss_bytes", 0)), _rss_bytes())

    for batch_number, batch_indices in enumerate(batches, start=1):
        try:
            batch_values, padded = _encode_exact_batch(
                model, tokenizer, torch, bodies, batch_indices, lengths,
                max_length=int(cfg["qwen"]["max_length"]),
            )
        except RuntimeError as error:
            if str(error).startswith("RESOURCE_EXCEPTION_CUDA_OOM"):
                write_json(RUNTIME / "resource-exception.json", {
                    "status": "RESOURCE_EXCEPTION",
                    "reason": str(error),
                    "completed": state["completed"],
                    "batch_size": BATCH_SIZE,
                    "partial_resume": False,
                    "signature": signature,
                })
            raise
        validate_embeddings(batch_values, rows=len(batch_indices), dimension=dimension)
        values[batch_indices] = batch_values
        elapsed = time.perf_counter() - started
        peak_rss = max(peak_rss, _rss_bytes())
        state.update(
            {
                "status": "IN_PROGRESS",
                "completed": int(batch_indices[-1]) + 1,
                "seconds": elapsed,
                "batches_completed": batch_number,
                "padded_tokens": int(state.get("padded_tokens", 0)) + padded,
                "true_tokens": int(state.get("true_tokens", 0)) + int(lengths[batch_indices].sum()),
                "gpu_peak_allocated_bytes": max(
                    int(state.get("gpu_peak_allocated_bytes", 0)), int(torch.cuda.max_memory_allocated())
                ),
                "gpu_peak_reserved_bytes": max(
                    int(state.get("gpu_peak_reserved_bytes", 0)), int(torch.cuda.max_memory_reserved())
                ),
                "process_peak_rss_bytes": peak_rss,
            }
        )
        if batch_number % 50 == 0 or batch_number == len(batches):
            values.flush()
            write_json(FULL_PROGRESS, state)
        require(state["seconds"] <= seconds_limit, "Qwen full encoding exceeded time limit")
        require(state["gpu_peak_allocated_bytes"] <= gpu_limit, "Qwen full encoding exceeded GPU limit")
        if peak_rss:
            require(peak_rss <= host_limit, "Qwen full encoding exceeded host memory limit")
        if batch_number % 250 == 0:
            print(
                f"QWEN {state['completed']}/{len(ids)} seconds={state['seconds']:.1f}",
                flush=True,
            )

    torch.cuda.synchronize()
    require(state["completed"] == len(ids), "Qwen full encoding is incomplete")
    for start in range(0, len(ids), 4096):
        validate_embeddings(np.asarray(values[start:start + 4096]),
                            rows=min(4096, len(ids) - start), dimension=dimension)
    state["seconds"] = time.perf_counter() - started
    require(state["seconds"] <= seconds_limit, "Qwen full encoding exceeded time limit before seal")
    runtime = _runtime_metadata(torch)
    del model, values
    gc.collect()
    torch.cuda.empty_cache()

    _atomic_save_npy(FINAL_IDS, ids)
    os.replace(PARTIAL_EMBEDDINGS, FINAL_EMBEDDINGS)
    final_values = np.load(FINAL_EMBEDDINGS, mmap_mode="r", allow_pickle=False)
    validation_parts: list[dict[str, Any]] = []
    for start in range(0, len(ids), 4096):
        validation_parts.append(
            validate_embeddings(
                np.asarray(final_values[start : start + 4096]),
                rows=min(4096, len(ids) - start),
                dimension=dimension,
            )
        )
    del final_values
    state["status"] = "COMPLETE"
    write_json(FULL_PROGRESS, state)
    summary = {
        "schema_version": 1,
        "status": "PASS",
        "movies": len(ids),
        "shape": [len(ids), dimension],
        "dtype": "float32",
        "movie_id_order": "STRICT_ASCENDING",
        "input_prefix": "",
        "query_instruction": None,
        "max_length": int(cfg["qwen"]["max_length"]),
        "truncated_documents": 0,
        "length_bucketing": False,
        "dynamic_padding": True,
        "batch_size": BATCH_SIZE,
        "batching": "CONSECUTIVE_CANONICAL_MOVIE_ID_NO_LENGTH_SORT",
        "partial_resume": False,
        "validation_chunks": len(validation_parts),
        "maximum_l2_error": max(part["l2_max_abs_error"] for part in validation_parts),
        "resources": {key: state[key] for key in (
            "seconds", "batches_completed", "padded_tokens", "true_tokens",
            "gpu_peak_allocated_bytes", "gpu_peak_reserved_bytes", "process_peak_rss_bytes",
        )},
        "runtime": runtime,
        "ratings_opened": False,
        "labels_opened": False,
        "network_requests": 0,
    }
    write_json(FINAL_SUMMARY, summary)
    seal = {
        "schema_version": 1,
        "status": "PASS",
        "parents": {
            "documents-seal.json": pin(DOCUMENT_SEAL),
            "model-seal.json": pin(MODEL_SEAL),
            "qwen-token-audit-seal.json": pin(TOKEN_AUDIT_SEAL),
            "qwen-pilot-seal.json": pin(PILOT_SEAL),
            "pilot-review.json": pin(PILOT_REVIEW),
        },
        "artifacts": {
            FINAL_IDS.name: pin(FINAL_IDS),
            FINAL_EMBEDDINGS.name: pin(FINAL_EMBEDDINGS),
            FINAL_SUMMARY.name: pin(FINAL_SUMMARY),
        },
        "implementation": _implementation_pins(),
        "pilot_review": pilot_review,
        "ratings_opened": False,
        "labels_opened": False,
    }
    write_json(FINAL_SEAL, seal)
    print("QWEN_FULL_PASS", json.dumps({"movies": len(ids), "seconds": state["seconds"]}), flush=True)
    return seal


def verify_full() -> dict[str, Any]:
    seal = read_json(FINAL_SEAL)
    require(seal.get("status") == "PASS", "Qwen full seal status")
    verify_document_seal()
    verify_model_seal()
    verify_token_audit()
    verify_pilot()
    pilot_review = verify_pilot_review()
    parents = seal.get("parents", {})
    require(parents.get("documents-seal.json") == pin(DOCUMENT_SEAL), "full document parent drift")
    require(parents.get("model-seal.json") == pin(MODEL_SEAL), "full model parent drift")
    require(parents.get("qwen-token-audit-seal.json") == pin(TOKEN_AUDIT_SEAL),
            "full token-audit parent drift")
    require(parents.get("qwen-pilot-seal.json") == pin(PILOT_SEAL), "full pilot parent drift")
    require(parents.get("pilot-review.json") == pin(PILOT_REVIEW), "full pilot-review parent drift")
    require(seal.get("pilot_review") == pilot_review, "full embedded pilot review drift")
    require(seal.get("implementation") == _implementation_pins(), "full implementation drift")
    for name, expected in seal["artifacts"].items():
        require(pin(OUT / name) == expected, f"Qwen full artifact drift: {name}")
    ids = np.load(FINAL_IDS, allow_pickle=False)
    values = np.load(FINAL_EMBEDDINGS, mmap_mode="r", allow_pickle=False)
    cfg = load_config()
    require(ids.shape == (int(cfg["catalog_movies"]),) and np.array_equal(ids, np.sort(np.unique(ids))),
            "Qwen final ID axis drift")
    require(values.shape == (len(ids), int(cfg["qwen"]["dimension"])) and values.dtype == np.float32,
            "Qwen final matrix shape/dtype drift")
    return seal


def main() -> None:
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    parser = argparse.ArgumentParser(description="Build pinned hybrid345 Qwen embeddings")
    parser.add_argument("action", choices=("download", "token-audit", "pilot", "full", "verify"))
    parser.add_argument("--tokenizer-batch-size", type=int, default=256)
    args = parser.parse_args()
    if args.action == "download":
        prepare_model()
    elif args.action == "token-audit":
        audit_tokens(tokenizer_batch_size=args.tokenizer_batch_size)
    elif args.action == "pilot":
        run_pilot()
    elif args.action == "full":
        run_full()
    else:
        verify_full()


if __name__ == "__main__":
    main()
