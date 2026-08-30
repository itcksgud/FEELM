from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import socket
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import numpy as np
import uvicorn

from .api import ArtifactRegistry, create_app
from .artifact_set import export_fixture_artifact_set, load_artifact_set
from .factors import ItemFactorModel


BENCHMARK_VERSION = "rec-ev-007-v1"
PINNED_HTTPX_VERSION = "0.28.1"
RESULT_CHECKSUM_POLICY = (
    "FULL_RESULT_BYTES_INCLUDE_GENERATED_AT_ENVIRONMENT_AND_OBSERVED_TIMINGS;_"
    "CHECKSUM_CHANGES_BY_DESIGN"
)
PROTOCOL_HASH_EXCLUDES = (
    "generated_at",
    "environment",
    "artifact_lifecycle.observed_metrics",
    "serving_http.observed_metrics",
    "inactive_fold_in_core.observed_metrics",
    "gate_results",
    "technical_recommendation",
)
CANDIDATE_COUNTS = (10, 100, 1000)
RATING_K_VALUES = (0, 1, 3, 5, 10, 20)
CONCURRENCY_LEVELS = (1, 4, 8)
HEALTHY_OUTBOX_POLL_INTERVAL_MS = 1000

# Locked before measurement. These are conservative local-loopback engineering gates,
# not a production SLA and not evidence that expected-star should be enabled.
PREDECLARED_GATES = {
    "serving_candidate_le_100_p95_ms": 250.0,
    "serving_candidate_1000_p95_ms": 1000.0,
    "serving_concurrency_4_p95_ms": 500.0,
    "serving_concurrency_4_min_throughput_rps": 20.0,
    "readiness_p95_ms": 50.0,
    "artifact_reload_p95_ms": 2000.0,
    "inactive_fold_in_core_p95_ms": 100.0,
}

RECOMMENDATION_RULE = {
    "timeout_multiplier_over_relevant_p99": 3.0,
    "timeout_rounding_ms": 50,
    "timeout_floor_ms": 750,
    "timeout_ceiling_ms": 2000,
    "healthy_freshness_floor_ms": 3000,
    "healthy_freshness_rounding_ms": 500,
    "healthy_freshness_formula": "max(3000, outbox_poll_ms + 2 * timeout_ms)",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _protocol_payload(result: dict[str, object]) -> dict[str, object]:
    """Return only pre-measurement protocol fields; never runtime identity or timings."""
    return {
        "benchmark_version": result["benchmark_version"],
        "scope": result["scope"],
        "conditions": result["conditions"],
        "predeclared_gates": result["predeclared_gates"],
        "recommendation_rule": result["recommendation_rule"],
    }


def _protocol_sha256(result: dict[str, object]) -> str:
    canonical = json.dumps(
        _protocol_payload(result), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _percentile(values: list[float], quantile: float) -> float:
    if not values or quantile <= 0 or quantile > 1:
        raise ValueError("non-empty values and a quantile in (0, 1] are required")
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _summary(durations_ms: list[float], wall_seconds: float) -> dict[str, float | int]:
    if not durations_ms or wall_seconds <= 0:
        raise ValueError("benchmark timings are invalid")
    return {
        "requests": len(durations_ms),
        "p50_ms": round(_percentile(durations_ms, 0.50), 6),
        "p95_ms": round(_percentile(durations_ms, 0.95), 6),
        "p99_ms": round(_percentile(durations_ms, 0.99), 6),
        "mean_ms": round(sum(durations_ms) / len(durations_ms), 6),
        "max_ms": round(max(durations_ms), 6),
        "throughput_rps": round(len(durations_ms) / wall_seconds, 6),
    }


def _measure(operation: Callable[[], None], warmup: int, iterations: int) -> dict[str, float | int]:
    if warmup < 0 or iterations < 2:
        raise ValueError("benchmark requires non-negative warmup and at least two iterations")
    for _ in range(warmup):
        operation()
    durations: list[float] = []
    wall_start = time.perf_counter()
    for _ in range(iterations):
        started = time.perf_counter_ns()
        operation()
        durations.append((time.perf_counter_ns() - started) / 1_000_000.0)
    return _summary(durations, time.perf_counter() - wall_start)


def _measure_concurrent(
    operation: Callable[[], None], warmup: int, iterations: int, workers: int
) -> dict[str, float | int]:
    if workers < 1:
        raise ValueError("concurrency must be positive")
    for _ in range(warmup):
        operation()

    start = threading.Event()

    def timed() -> float:
        start.wait()
        began = time.perf_counter_ns()
        operation()
        return (time.perf_counter_ns() - began) / 1_000_000.0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(timed) for _ in range(iterations)]
        wall_start = time.perf_counter()
        start.set()
        durations = [future.result() for future in futures]
        wall_seconds = time.perf_counter() - wall_start
    return _summary(durations, wall_seconds)


class _LoopbackUvicorn:
    def __init__(self, app: Any) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._port = int(self._socket.getsockname()[1])
        config = uvicorn.Config(app, log_level="critical", access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [self._socket]}, daemon=True
        )

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def __enter__(self) -> "_LoopbackUvicorn":
        self._thread.start()
        deadline = time.monotonic() + 10.0
        while not self._server.started and self._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not self._server.started:
            raise RuntimeError("loopback Uvicorn did not start")
        return self

    def __exit__(self, *_: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10.0)
        self._socket.close()


def _request_payload(
    movie_ids: list[str], rating_k: int, *, rating_movie_ids: list[str] | None = None
) -> dict[str, object]:
    request_id = "10000000-0000-0000-0000-000000000007"
    rating_source = rating_movie_ids if rating_movie_ids is not None else movie_ids
    if len(rating_source) < rating_k:
        raise ValueError("benchmark Rating fixture does not cover K")
    ratings = [
        {"movieId": rating_source[index], "value": 1 + index % 5, "revision": 1}
        for index in range(rating_k)
    ]
    return {
        "requestId": request_id,
        "candidateSet": {
            "candidateSetVersion": f"rec-ev-007-candidates-{len(movie_ids)}-v1",
            "movieIds": movie_ids,
        },
        "preferenceInput": {
            "inputVersion": f"rec-ev-007-ratings-k{rating_k}-v1",
            "ratings": ratings,
        },
        "starPolicy": "DISABLED",
    }


def _load_evidence_factors(
    factor_path: Path, evidence_manifest_path: Path
) -> tuple[ItemFactorModel, dict[str, object]]:
    evidence = json.loads(evidence_manifest_path.read_text(encoding="utf-8"))
    artifact = evidence["artifacts"]["cohort_excluded_item_factors"]
    actual_checksum = _sha256(factor_path)
    if actual_checksum != artifact["sha256"]:
        raise ValueError("fold-in evidence artifact checksum does not match its manifest")
    with np.load(factor_path, allow_pickle=False) as payload:
        item_ids = np.asarray(payload["movie_ids"], dtype=np.int64)
        factors = np.asarray(payload["movie_factors"], dtype=np.float64)
    reg_param = float(evidence["model"]["als"]["reg_param"])
    model = ItemFactorModel(item_ids=item_ids, factors=factors, reg_param=reg_param)
    return model, {
        "evidence_id": str(evidence["evidence_id"]),
        "payload_sha256": actual_checksum,
        "item_count": len(item_ids),
        "factor_rank": model.rank,
        "coverage": "REC_EV_003_COHORT_EXCLUDED_EVIDENCE_NOT_PRODUCT_TRAFFIC",
    }


def _fold_in_scenarios(
    model: ItemFactorModel, warmup: int, iterations: int
) -> list[dict[str, object]]:
    scenarios: list[dict[str, object]] = []
    ratings = np.asarray([1 + index % 5 for index in range(max(RATING_K_VALUES))], dtype=np.float64)
    for rating_k in RATING_K_VALUES:
        if rating_k == 0:
            scenarios.append(
                {
                    "rating_k": 0,
                    "candidate_count": None,
                    "status": "NOT_APPLICABLE_NO_FOLD_IN",
                }
            )
            continue
        onboarding = model.item_ids[:rating_k]
        for candidate_count in CANDIDATE_COUNTS:
            targets = model.item_ids[-candidate_count:]

            def operation() -> None:
                result = model.fold_in(onboarding, ratings[:rating_k])
                if result.factor is None or result.factor_count != rating_k:
                    raise RuntimeError("fold-in fixture unexpectedly lost known factors")
                scores, known = model.score(result.factor, targets)
                if len(scores) != candidate_count or not bool(known.all()):
                    raise RuntimeError("fold-in score fixture is incomplete")

            metrics = _measure(operation, warmup, iterations)
            scenarios.append(
                {
                    "rating_k": rating_k,
                    "candidate_count": candidate_count,
                    "status": "MEASURED_INACTIVE_CORE",
                    **metrics,
                }
            )
    return scenarios


def _gate_results(result: dict[str, object]) -> dict[str, bool]:
    serving = result["serving_http"]["sequential"]
    concurrency = result["serving_http"]["concurrency"]
    active_fold_in = [
        row for row in result["inactive_fold_in_core"]["scenarios"]
        if row["status"] == "MEASURED_INACTIVE_CORE"
    ]
    concurrency_four = next(row for row in concurrency if row["concurrency"] == 4)
    return {
        "serving_candidate_le_100": all(
            row["p95_ms"] <= PREDECLARED_GATES["serving_candidate_le_100_p95_ms"]
            for row in serving if row["candidate_count"] <= 100
        ),
        "serving_candidate_1000": all(
            row["p95_ms"] <= PREDECLARED_GATES["serving_candidate_1000_p95_ms"]
            for row in serving if row["candidate_count"] == 1000
        ),
        "serving_concurrency_4": (
            concurrency_four["p95_ms"] <= PREDECLARED_GATES["serving_concurrency_4_p95_ms"]
            and concurrency_four["throughput_rps"]
            >= PREDECLARED_GATES["serving_concurrency_4_min_throughput_rps"]
        ),
        "readiness": result["artifact_lifecycle"]["readiness"]["p95_ms"]
        <= PREDECLARED_GATES["readiness_p95_ms"],
        "artifact_reload": result["artifact_lifecycle"]["valid_atomic_reload"]["p95_ms"]
        <= PREDECLARED_GATES["artifact_reload_p95_ms"],
        "inactive_fold_in_core": all(
            row["p95_ms"] <= PREDECLARED_GATES["inactive_fold_in_core_p95_ms"]
            for row in active_fold_in
        ),
    }


def _technical_recommendation(result: dict[str, object]) -> dict[str, object]:
    relevant = [
        row["p99_ms"] for row in result["serving_http"]["sequential"]
        if row["candidate_count"] <= 100
    ]
    relevant.extend(
        row["p99_ms"] for row in result["serving_http"]["concurrency"]
        if row["concurrency"] <= 4
    )
    observed = max(relevant)
    rounding = int(RECOMMENDATION_RULE["timeout_rounding_ms"])
    calculated = math.ceil(
        observed * float(RECOMMENDATION_RULE["timeout_multiplier_over_relevant_p99"]) / rounding
    ) * rounding
    timeout = max(int(RECOMMENDATION_RULE["timeout_floor_ms"]), calculated)
    timeout = min(int(RECOMMENDATION_RULE["timeout_ceiling_ms"]), timeout)
    freshness_raw = HEALTHY_OUTBOX_POLL_INTERVAL_MS + 2 * timeout
    freshness_rounding = int(RECOMMENDATION_RULE["healthy_freshness_rounding_ms"])
    freshness = max(
        int(RECOMMENDATION_RULE["healthy_freshness_floor_ms"]),
        math.ceil(freshness_raw / freshness_rounding) * freshness_rounding,
    )
    gates = result["gate_results"]
    return {
        "status": "LOCAL_LOOPBACK_PROVISIONAL" if all(gates.values()) else "BLOCKED_BY_LOCAL_GATE_FAILURE",
        "spring_outbound_timeout_ms": timeout,
        "active_rating_snapshot_healthy_path_target_ms": freshness,
        "stale_success_fallback": "DISABLED",
        "expected_star_activation": "PROHIBITED_BY_DN_C2_008",
        "basis": "predeclared_rule_applied_to_loopback_http_p99",
        "production_validation_required": True,
    }


def run_benchmark(
    *,
    factor_path: Path,
    factor_manifest_path: Path,
    fixture_item_count: int = 1000,
    warmup: int = 20,
    serving_iterations: int = 120,
    concurrent_iterations: int = 240,
    reload_iterations: int = 30,
    fold_in_iterations: int = 300,
) -> dict[str, object]:
    installed_httpx = importlib.metadata.version("httpx")
    if installed_httpx != PINNED_HTTPX_VERSION:
        raise RuntimeError(
            f"REC-EV-007 requires pinned httpx {PINNED_HTTPX_VERSION}; "
            f"installed version is {installed_httpx}"
        )
    if fixture_item_count < max(CANDIDATE_COUNTS):
        raise ValueError("benchmark fixture must cover the maximum candidate count")
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    factor_model, factor_source = _load_evidence_factors(factor_path, factor_manifest_path)

    with tempfile.TemporaryDirectory(prefix="feelm-rec-ev-007-") as temporary:
        root = Path(temporary)
        manifest = export_fixture_artifact_set(
            root / "ready", fixture_item_count=fixture_item_count
        )
        loaded = load_artifact_set(manifest)
        movie_ids = [
            loaded.core.item_mapping.by_movielens_id.get(item_id)
            for item_id in range(1, fixture_item_count + 1)
        ]
        if any(value is None for value in movie_ids):
            raise RuntimeError("benchmark mapping is incomplete")
        safe_movie_ids = [str(value) for value in movie_ids if value is not None]

        load_metrics = _measure(lambda: load_artifact_set(manifest), warmup, reload_iterations)
        registry = ArtifactRegistry()
        if not registry.reload(manifest):
            raise RuntimeError("benchmark registry did not accept the ready fixture")
        reload_metrics = _measure(lambda: _require_reload(registry, manifest), warmup, reload_iterations)

        invalid_root = root / "invalid"
        shutil.copytree(root / "ready", invalid_root)
        invalid_manifest = invalid_root / "artifact-set.json"
        with (invalid_root / "bias.npz").open("ab") as stream:
            stream.write(b"invalid-reload-fixture")
        active_before = registry.snapshot().artifact_set.artifact_set_version
        invalid_started = time.perf_counter_ns()
        invalid_accepted = registry.reload(invalid_manifest)
        invalid_latency_ms = (time.perf_counter_ns() - invalid_started) / 1_000_000.0
        invalid_retained = (
            not invalid_accepted
            and registry.snapshot().artifact_set is not None
            and registry.snapshot().artifact_set.artifact_set_version == active_before
        )
        if not invalid_retained:
            raise RuntimeError("invalid artifact reload did not retain the previous ready set")

        app = create_app(artifact_manifest=manifest, auth_mode="fake")
        with _LoopbackUvicorn(app) as server, httpx.Client(
            base_url=server.base_url,
            timeout=10.0,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=64),
        ) as client:
            auth_headers = {"Authorization": "Bearer test-c2-service-token"}

            def readiness() -> None:
                response = client.get("/internal/health/ready", headers=auth_headers)
                if response.status_code != 200 or response.json().get("status") != "READY":
                    raise RuntimeError("readiness benchmark request failed")

            readiness_metrics = _measure(readiness, warmup, serving_iterations)
            sequential: list[dict[str, object]] = []
            for candidate_count in CANDIDATE_COUNTS:
                selected = safe_movie_ids[:candidate_count]
                for rating_k in RATING_K_VALUES:
                    payload = _request_payload(
                        selected, rating_k, rating_movie_ids=safe_movie_ids
                    )

                    def rank() -> None:
                        request_id = str(payload["requestId"])
                        response = client.post(
                            "/internal/v1/recommendations/rank",
                            headers={**auth_headers, "X-Request-Id": request_id},
                            json=payload,
                        )
                        body = response.json()
                        if (
                            response.status_code != 200
                            or body.get("outcome") != "COMPLETE"
                            or body.get("snapshot", {}).get("rankingAlpha") != 0.0
                            or len(body.get("items", [])) != candidate_count
                        ):
                            raise RuntimeError("Popularity serving benchmark invariant failed")

                    sequential.append(
                        {
                            "candidate_count": candidate_count,
                            "rating_k": rating_k,
                            "concurrency": 1,
                            "active_path": "UVICORN_LOOPBACK_POPULARITY_ALPHA_0_STAR_DISABLED",
                            **_measure(rank, warmup, serving_iterations),
                        }
                    )

            concurrency_rows: list[dict[str, object]] = []
            payload = _request_payload(
                safe_movie_ids[:100], 10, rating_movie_ids=safe_movie_ids
            )

            def concurrent_rank() -> None:
                request_id = str(payload["requestId"])
                response = client.post(
                    "/internal/v1/recommendations/rank",
                    headers={**auth_headers, "X-Request-Id": request_id},
                    json=payload,
                )
                if response.status_code != 200 or response.json().get("outcome") != "COMPLETE":
                    raise RuntimeError("concurrent Popularity request failed")

            for workers in CONCURRENCY_LEVELS:
                concurrency_rows.append(
                    {
                        "candidate_count": 100,
                        "rating_k": 10,
                        "concurrency": workers,
                        **_measure_concurrent(
                            concurrent_rank, warmup, concurrent_iterations, workers
                        ),
                    }
                )

        result: dict[str, object] = {
            "schema_version": 1,
            "evidence_id": "REC-EV-007",
            "benchmark_version": BENCHMARK_VERSION,
            "generated_at": generated_at,
            "scope": {
                "active_serving": "FASTAPI_UVICORN_LOOPBACK_BAYESIAN_POPULARITY_ONLY_ALPHA_0",
                "inactive_diagnostic": "REC_EV_003_ITEM_FACTOR_FOLD_IN_CORE_NOT_CALLED_BY_C2A_HTTP",
                "fixture_coverage": "SYNTHETIC_SERVICE_UUID_FIXTURE_NOT_CATALOG_OR_PRODUCT_TRAFFIC",
                "network_excluded": "REMOTE_NETWORK_TLS_LOAD_BALANCER_AND_SPRING_CLIENT",
            },
            "environment": {
                "python": platform.python_version(),
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "logical_cpu_count": os.cpu_count(),
                "packages": {
                    name: importlib.metadata.version(name)
                    for name in ("fastapi", "httpx", "numpy", "uvicorn")
                },
                "timer": "time.perf_counter_ns",
            },
            "conditions": {
                "warmup_per_scenario": warmup,
                "serving_iterations_per_scenario": serving_iterations,
                "concurrent_requests_per_scenario": concurrent_iterations,
                "reload_iterations": reload_iterations,
                "fold_in_iterations_per_scenario": fold_in_iterations,
                "candidate_counts": list(CANDIDATE_COUNTS),
                "rating_k_values": list(RATING_K_VALUES),
                "concurrency_levels": list(CONCURRENCY_LEVELS),
            },
            "predeclared_gates": PREDECLARED_GATES,
            "recommendation_rule": RECOMMENDATION_RULE,
            "artifact_lifecycle": {
                "fixture_item_count": fixture_item_count,
                "artifact_set_kind": loaded.set_kind,
                "initial_load_validate": load_metrics,
                "valid_atomic_reload": reload_metrics,
                "invalid_reload": {
                    "accepted": False,
                    "previous_ready_retained": invalid_retained,
                    "safe_reason_code": "ARTIFACT_COMPATIBILITY_FAILURE",
                    "latency_ms": round(invalid_latency_ms, 6),
                },
                "readiness": readiness_metrics,
            },
            "serving_http": {
                "ranking_policy": "BAYESIAN_POPULARITY_ONLY",
                "ranking_alpha": 0.0,
                "star_policy": "DISABLED",
                "sequential": sequential,
                "concurrency": concurrency_rows,
            },
            "inactive_fold_in_core": {
                "source": factor_source,
                "warning": "DIAGNOSTIC_ONLY_NOT_ACTIVE_RANKING_OR_PRODUCT_STAR",
                "scenarios": _fold_in_scenarios(factor_model, warmup, fold_in_iterations),
            },
            "privacy": {
                "raw_user_ids_persisted": False,
                "raw_movie_ids_persisted": False,
                "request_ids_persisted": False,
                "rating_values_persisted": False,
                "tokens_persisted": False,
                "host_paths_persisted": False,
            },
            "reproducibility": {
                "protocol_hash_scope": "PRE_MEASUREMENT_CONFIGURATION_ONLY",
                "protocol_hash_excludes": list(PROTOCOL_HASH_EXCLUDES),
                "result_checksum_policy": RESULT_CHECKSUM_POLICY,
                "source_metadata_policy": "CHECKSUM_AND_SHAPE_ONLY_NO_RAW_FACTOR_PATH",
            },
        }
        result["gate_results"] = _gate_results(result)
        result["technical_recommendation"] = _technical_recommendation(result)
        return result


def _require_reload(registry: ArtifactRegistry, manifest: Path) -> None:
    if not registry.reload(manifest):
        raise RuntimeError("valid artifact reload failed")


def write_result(result: dict[str, object], result_path: Path, manifest_path: Path) -> None:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    result_bytes = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    result_path.write_bytes(result_bytes)
    manifest = {
        "schema_version": 1,
        "evidence_id": "REC-EV-007",
        "benchmark_version": BENCHMARK_VERSION,
        "generated_at": result["generated_at"],
        "result_file": result_path.name,
        "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "result_checksum_policy": RESULT_CHECKSUM_POLICY,
        "protocol_sha256": _protocol_sha256(result),
        "protocol_hash_excludes": list(PROTOCOL_HASH_EXCLUDES),
        "source_factor_evidence": result["inactive_fold_in_core"]["source"],
        "source_metadata_policy": "CHECKSUM_AND_SHAPE_ONLY_NO_RAW_FACTOR_PATH",
        "conditions": result["conditions"],
        "predeclared_gates": result["predeclared_gates"],
        "gate_results": result["gate_results"],
        "technical_recommendation": result["technical_recommendation"],
        "privacy": result["privacy"],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run REC-EV-007 FastAPI/Fold-in benchmark")
    parser.add_argument("--factor-artifact", type=Path, required=True)
    parser.add_argument("--factor-manifest", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--serving-iterations", type=int, default=120)
    parser.add_argument("--concurrent-iterations", type=int, default=240)
    parser.add_argument("--reload-iterations", type=int, default=30)
    parser.add_argument("--fold-in-iterations", type=int, default=300)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_benchmark(
        factor_path=args.factor_artifact,
        factor_manifest_path=args.factor_manifest,
        warmup=args.warmup,
        serving_iterations=args.serving_iterations,
        concurrent_iterations=args.concurrent_iterations,
        reload_iterations=args.reload_iterations,
        fold_in_iterations=args.fold_in_iterations,
    )
    write_result(result, args.result, args.manifest)
    print(json.dumps({
        "status": "PASS" if all(result["gate_results"].values()) else "GATE_FAILURE",
        "evidence_id": result["evidence_id"],
        "benchmark_version": result["benchmark_version"],
        "gate_results": result["gate_results"],
        "technical_recommendation": result["technical_recommendation"],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if all(result["gate_results"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
