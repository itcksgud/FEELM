#!/usr/bin/env python3
"""Independently verify REC-EV-019E routing, metrics, bootstrap, and post-hoc status."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-019e-no-retune-incremental-applicability-gate.json"
DEFAULT_PREREGISTRATION = ROOT / "docs/recommendation/evidence/REC-EV-019E-no-retune-incremental-applicability-preregistration.md"
DEFAULT_MANIFEST = ROOT / "docs/recommendation/evidence/manifests/rec-ev-019e-validation.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text_contract(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def close(actual: float, expected: float, *, tolerance: float = 1e-12) -> bool:
    return math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)


def route_for_stratum(stratum: str) -> tuple[str, str]:
    routes = {
        "BOTH_LIGHTFM": ("K5", "K5_FOLD_IN"),
        "K10_NEWLY_APPLICABLE": ("K10", "K10_FOLD_IN"),
        "BOTH_FALLBACK": ("K5", "B0"),
    }
    if stratum not in routes:
        raise RuntimeError(f"unknown applicability stratum: {stratum}")
    return routes[stratum]


def bootstrap_paired(ndcg: np.ndarray, harm: np.ndarray, *, iterations: int, seed: int) -> dict[str, Any]:
    ndcg_values = np.asarray(ndcg, dtype=np.float64)
    harm_values = np.asarray(harm, dtype=np.float64)
    rng = np.random.default_rng(int(seed))
    ndcg_means = np.empty(int(iterations), dtype=np.float64)
    harm_means = np.empty(int(iterations), dtype=np.float64)
    offset = 0
    while offset < int(iterations):
        stop = min(offset + 250, int(iterations))
        indices = rng.integers(0, len(ndcg_values), size=(stop - offset, len(ndcg_values)))
        ndcg_means[offset:stop] = ndcg_values[indices].mean(axis=1)
        harm_means[offset:stop] = harm_values[indices].mean(axis=1)
        offset = stop
    return {
        "iterations": int(iterations),
        "seed": int(seed),
        "ndcg_mean": float(ndcg_values.mean()),
        "ndcg_two_sided_95": [float(np.percentile(ndcg_means, 2.5)), float(np.percentile(ndcg_means, 97.5))],
        "harm_mean": float(harm_values.mean()),
        "harm_one_sided_95_upper": float(np.percentile(harm_means, 95.0)),
    }


def decision(bootstrap: Mapping[str, Any]) -> dict[str, str]:
    if float(bootstrap["harm_one_sided_95_upper"]) > 0.005:
        return {"status": "FAIL_SAFETY_MARGIN_EXCEEDED", "reason": "HARM_UPPER_EXCEEDS_0_005"}
    if float(bootstrap["ndcg_mean"]) >= 0.005 and float(bootstrap["ndcg_two_sided_95"][0]) > 0.0:
        return {
            "status": "PASS_POST_HOC_VALIDATION_REQUIRES_FRESH_CONFIRMATION",
            "reason": "POST_HOC_EFFICACY_AND_SAFETY_THRESHOLDS_MET",
        }
    return {"status": "INCONCLUSIVE_POST_HOC_VALIDATION", "reason": "POST_HOC_SUCCESS_NOT_ESTABLISHED"}


def metric_payload(row: Any) -> dict[str, Any]:
    return {
        "ndcg_at_10": float(row.ndcg_at_10),
        "recall_at_10": float(row.recall_at_10),
        "mrr_at_10": float(row.mrr_at_10),
        "candidate_recall_at_500": float(row.candidate_recall_at_500),
        "positive_mean_rank_percentile": None if pd.isna(row.positive_mean_rank_percentile) else float(row.positive_mean_rank_percentile),
        "harm_at_2": bool(row.harm_at_2),
        "fallback_user": bool(row.fallback_user),
        "applicable_user": not bool(row.fallback_user),
    }


def benefit_harm_counts(frame: pd.DataFrame) -> dict[str, int]:
    values = frame["delta_ndcg_at_10"].to_numpy(dtype=np.float64)
    return {
        "benefit": int(np.count_nonzero(values > 0.0)),
        "neutral": int(np.count_nonzero(values == 0.0)),
        "harm": int(np.count_nonzero(values < 0.0)),
    }


def verify(manifest_path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    contract_path = root / DEFAULT_CONTRACT.relative_to(ROOT)
    expected_manifest = root / DEFAULT_MANIFEST.relative_to(ROOT)
    require(manifest_path.resolve() == expected_manifest.resolve(), "unexpected manifest path")
    contract = read_json(contract_path)
    manifest = read_json(manifest_path)
    output_root = root / contract["output_root"]
    lock = read_json(output_root / contract["outputs"]["protocol_lock"])
    progress = read_json(output_root / contract["outputs"]["progress"])
    result = read_json(output_root / contract["outputs"]["result"])
    source_manifest = read_json(output_root / contract["outputs"]["source_manifest"])
    require(manifest["evidence_id"] == "REC-EV-019E", "evidence identity drift")
    require(sha256_text_contract(contract_path) == manifest["contract_sha256"] == lock["contract_sha256"], "contract hash drift")
    require(sha256_text_contract(root / DEFAULT_PREREGISTRATION.relative_to(ROOT)) == lock["preregistration_sha256"], "preregistration hash drift")
    require(lock["future_metrics_read"] is False, "lock did not precede 019E metrics")
    require(lock["rec_ev_019d_result_and_harm_decomposition_already_observed"] is True, "post-hoc source disclosure missing")
    require(int(lock["created_at_epoch_ns"]) < int(progress["run_started_epoch_ns"]), "lock timestamp did not precede run")
    require(source_manifest["created_before_019e_hybrid_metrics"] is True, "source manifest timing drift")
    require(source_manifest["rec_ev_019d_result_and_harm_decomposition_already_observed"] is True, "source post-hoc disclosure drift")
    for name, entry in lock["source_code"].items():
        path = root / entry["path"]
        require(path.is_file() and sha256_file(path) == entry["sha256"], f"locked source drift: {name}")
    for name, expected in contract["allowed_input_artifacts"].items():
        path = root / expected["path"]
        require(path.is_file(), f"missing source: {name}")
        require(path.stat().st_size == int(expected["bytes"]), f"source size drift: {name}")
        require(sha256_file(path) == expected["sha256"], f"source hash drift: {name}")
        require(manifest["source_checksums"][name] == expected["sha256"], f"manifest source hash drift: {name}")
    for artifact in manifest["artifacts"]:
        path = root / artifact["path"]
        require(path.is_file(), f"missing output: {artifact['path']}")
        require(path.stat().st_size == int(artifact["bytes"]), f"output size drift: {artifact['path']}")
        require(sha256_file(path) == artifact["sha256"], f"output hash drift: {artifact['path']}")

    source_cohort = pq.read_table(root / contract["allowed_input_artifacts"]["rec_ev_019d_cohort"]["path"]).to_pandas()
    source_metrics = pq.read_table(root / contract["allowed_input_artifacts"]["rec_ev_019d_user_arm_metrics"]["path"]).to_pandas()
    source_metrics = source_metrics[source_metrics["estimand"] == "COMMON_K10_SEEN_MASK"]
    predictions = pq.read_table(
        root / contract["allowed_input_artifacts"]["rec_ev_019d_predictions"]["path"],
        filters=[("estimand", "=", "COMMON_K10_SEEN_MASK")],
    ).to_pandas()
    require(len(source_cohort) == 1479 and int(source_cohort["confirmatory"].sum()) == 1053, "source cohort drift")
    require(len(source_metrics) == 2958 and len(predictions) == 1479 * 2 * 500, "source ranking/metric count drift")
    metric_lookup = {(str(row.user_key), str(row.arm)): row for row in source_metrics.itertuples(index=False)}
    prediction_lookup: dict[tuple[str, str], tuple[tuple[int, ...], tuple[float, ...]]] = {}
    boundary_ties = 0
    for (user_key, arm), group in predictions.groupby(["user_key", "arm"], sort=False):
        ranked = group.sort_values("rank", kind="stable")
        require(ranked["rank"].astype(int).tolist() == list(range(1, 501)), "source Top-500 rank drift")
        scores = ranked["effective_score"].to_numpy(dtype=np.float32)
        movies = ranked["movie_id"].to_numpy(dtype=np.int64)
        require(bool(np.all(scores[1:] <= scores[:-1])), "source score order drift")
        ties = scores[1:] == scores[:-1]
        require(bool(np.all(movies[1:][ties] > movies[:-1][ties])), "source tie-break drift")
        if len(scores) == 500 and scores[-1] == scores[-2]:
            boundary_ties += 1
        prediction_lookup[(str(user_key), str(arm))] = (
            tuple(map(int, movies[:10])), tuple(map(float, scores[:10])),
        )
    require(len(prediction_lookup) == 2958, "source prediction key drift")

    output_cohort = pq.read_table(output_root / contract["outputs"]["cohort"]).to_pandas()
    routing = pq.read_table(output_root / contract["outputs"]["routing"]).to_pandas()
    output_metrics = pq.read_table(output_root / contract["outputs"]["user_arm_metrics"]).to_pandas()
    output_paired = pq.read_table(output_root / contract["outputs"]["paired_deltas"]).to_pandas()
    require(len(output_cohort) == 1479 and len(routing) == 1479, "output cohort/routing count drift")
    require(len(output_metrics) == 2958 and len(output_paired) == 1479, "output metric/delta count drift")
    output_cohort_lookup = {str(row.user_key): row for row in output_cohort.itertuples(index=False)}
    routing_lookup = {str(row.user_key): row for row in routing.itertuples(index=False)}
    output_metric_lookup = {(str(row.user_key), str(row.variant)): row for row in output_metrics.itertuples(index=False)}
    output_paired_lookup = {str(row.user_key): row for row in output_paired.itertuples(index=False)}

    recomputed_rows: list[dict[str, Any]] = []
    selected_top10_digest = hashlib.sha256()
    for source_row in source_cohort.sort_values("user_key", kind="stable").itertuples(index=False):
        user_key = str(source_row.user_key)
        stratum = str(source_row.applicability_stratum)
        candidate_arm, candidate_model = route_for_stratum(stratum)
        cohort_row = output_cohort_lookup[user_key]
        route = routing_lookup[user_key]
        require(str(cohort_row.applicability_stratum) == stratum, "cohort stratum drift")
        require(str(cohort_row.candidate_source_arm) == candidate_arm and str(cohort_row.candidate_model) == candidate_model, "cohort route drift")
        require(str(route.comparator_source_arm) == "K5" and str(route.candidate_source_arm) == candidate_arm, "routing arm drift")
        require(str(route.candidate_model) == candidate_model and int(route.parameter_count) == 0, "routing model/parameter drift")
        require(str(route.seen_mask) == "COMMON_K10_CANDIDATE_VALID_SEEN_MASK", "routing seen-mask drift")
        comparator = metric_payload(metric_lookup[(user_key, "K5")])
        candidate = metric_payload(metric_lookup[(user_key, candidate_arm)])
        for variant, source_arm, values in (("COMPARATOR", "K5", comparator), ("CANDIDATE", candidate_arm, candidate)):
            reported = output_metric_lookup[(user_key, variant)]
            require(str(reported.source_arm) == source_arm, "output metric source-arm drift")
            for name in ("ndcg_at_10", "recall_at_10", "mrr_at_10", "candidate_recall_at_500"):
                require(close(values[name], getattr(reported, name)), f"metric drift: {user_key} {variant} {name}")
            if values["positive_mean_rank_percentile"] is None:
                require(pd.isna(reported.positive_mean_rank_percentile), "positive rank null drift")
            else:
                require(close(values["positive_mean_rank_percentile"], reported.positive_mean_rank_percentile), "positive rank drift")
            require(bool(values["harm_at_2"]) == bool(reported.harm_at_2), "Harm@2 drift")
            require(bool(values["fallback_user"]) == bool(reported.fallback_user), "fallback drift")
        comparator_top10 = prediction_lookup[(user_key, "K5")]
        candidate_top10 = prediction_lookup[(user_key, candidate_arm)]
        selected_top10_digest.update(json.dumps({
            "user_key": user_key,
            "comparator": comparator_top10,
            "candidate": candidate_top10,
        }, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        expected_delta = {
            "delta_ndcg_at_10": candidate["ndcg_at_10"] - comparator["ndcg_at_10"],
            "delta_recall_at_10": candidate["recall_at_10"] - comparator["recall_at_10"],
            "delta_mrr_at_10": candidate["mrr_at_10"] - comparator["mrr_at_10"],
            "delta_candidate_recall_at_500": candidate["candidate_recall_at_500"] - comparator["candidate_recall_at_500"],
            "delta_harm_at_2": float(candidate["harm_at_2"]) - float(comparator["harm_at_2"]),
            "delta_fallback_user": float(candidate["fallback_user"]) - float(comparator["fallback_user"]),
            "delta_applicable_user": float(candidate["applicable_user"]) - float(comparator["applicable_user"]),
        }
        paired = output_paired_lookup[user_key]
        for name, value in expected_delta.items():
            require(close(value, getattr(paired, name)), f"paired delta drift: {user_key} {name}")
        recomputed_rows.append({
            "user_key": user_key,
            "confirmatory": bool(source_row.confirmatory),
            "applicability_stratum": stratum,
            **expected_delta,
        })
    recomputed = pd.DataFrame(recomputed_rows)
    confirmatory = recomputed[recomputed["confirmatory"]]
    bootstrap = bootstrap_paired(
        confirmatory["delta_ndcg_at_10"].to_numpy(dtype=np.float64),
        confirmatory["delta_harm_at_2"].to_numpy(dtype=np.float64),
        iterations=int(contract["bootstrap"]["iterations"]),
        seed=int(contract["bootstrap"]["seed"]),
    )
    reported_bootstrap = result["paired_confirmatory"]["bootstrap"]
    for name in ("ndcg_mean", "harm_mean", "harm_one_sided_95_upper"):
        require(close(bootstrap[name], reported_bootstrap[name]), f"bootstrap drift: {name}")
    require(bool(np.allclose(bootstrap["ndcg_two_sided_95"], reported_bootstrap["ndcg_two_sided_95"], rtol=0.0, atol=1e-12)), "bootstrap interval drift")
    recomputed_decision = decision(bootstrap)
    require(recomputed_decision == result["decision"], "decision drift")
    require(result["status"] == recomputed_decision["status"], "result status drift")
    require(result["fresh_target_independent_validation_required"] is True, "fresh confirmation boundary drift")
    require(result["paired_confirmatory"]["benefit_harm_user_counts"] == benefit_harm_counts(confirmatory), "benefit/harm count drift")
    counts = confirmatory["applicability_stratum"].value_counts().sort_index().to_dict()
    require(counts == {"BOTH_FALLBACK": 115, "BOTH_LIGHTFM": 661, "K10_NEWLY_APPLICABLE": 277}, "strata drift")
    for payload in (manifest, lock, source_manifest, progress, result, read_json(output_root / contract["outputs"]["strata"])):
        require(payload["locked_test_used"] is False, "Locked Test invariant failed")
        require(payload["champion"] is None, "champion invariant failed")
        require(payload["product_policy_updated"] is False, "product policy invariant failed")
    return {
        "status": "PASS_INDEPENDENT_REC_EV_019E_RECOMPUTATION",
        "decision": recomputed_decision,
        "post_hoc": True,
        "confirmatory_users": 1053,
        "routing_counts": counts,
        "benefit_harm_user_counts": benefit_harm_counts(confirmatory),
        "primary_bootstrap": bootstrap,
        "selected_source_top10_sha256": selected_top10_digest.hexdigest(),
        "source_top500_with_internal_boundary_tie_count": boundary_ties,
        "fresh_target_independent_validation_required": True,
        "locked_test_used": False,
        "champion": None,
        "product_policy_updated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    manifest = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    print(json.dumps(verify(manifest), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
