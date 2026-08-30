from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psutil

from recommendation_baseline_calibration import predict_popularity
from recommendation_cold_start_blend import alpha_grid, stable_user_selection
from recommendation_cold_start_curve import onboarding_user_bias
from recommendation_exploration_full_catalog import artifact, canonical_bytes, deterministic_top_k, exact_artifact, sha256, write_json

PROTOCOL = "rec-ev-011-cold-foldin-full-catalog-v1"
K_VALUES = (1, 3, 5, 10, 20)
ALPHAS = tuple(float(value) for value in alpha_grid(0.1))
TOP_CANDIDATES = 500
TOP_K = 10
SEED = 42


def load_sources(args: argparse.Namespace) -> dict[str, Any]:
    cold = json.loads(args.cold_start_manifest.read_text(encoding="utf-8"))
    dual = json.loads(args.dual_head_manifest.read_text(encoding="utf-8"))
    baseline = json.loads(args.baseline_manifest.read_text(encoding="utf-8"))
    if cold.get("evidence_id") != "REC-EV-003" or dual.get("evidence_id") != "REC-EV-003B":
        raise RuntimeError("REC-EV-003/003B source mismatch")
    if tuple(dual["protocol"]["alpha_grid"]) != ALPHAS:
        raise RuntimeError("REC-EV-003B alpha grid changed")
    paths = {name: exact_artifact(cold["artifacts"][name]) for name in (
        "onboarding_first_20", "cohort_excluded_bias", "cohort_excluded_item_factors",
        "foldin_user_factors", "sampled_ranking",
    )}
    paths["rec_ev_002_bias"] = exact_artifact(baseline["artifacts"]["bias_parameters"])
    sampled = pd.read_parquet(paths["sampled_ranking"], columns=["user_id", "movie_id", "is_positive"])
    positives = sampled.loc[sampled["is_positive"] == 1, ["user_id", "movie_id"]].sort_values("user_id")
    if positives["user_id"].duplicated().any():
        raise RuntimeError("ranking cohort positive is not unique per user")
    selection = stable_user_selection(positives["user_id"].to_numpy(dtype=np.int64), SEED)
    if int(selection.sum()) != dual["protocol"]["ranking_selection_users"] or int((~selection).sum()) != dual["protocol"]["ranking_evaluation_users"]:
        raise RuntimeError("REC-EV-003B selection/evaluation split differs")
    bias = np.load(paths["cohort_excluded_bias"], allow_pickle=False)
    baseline_bias = np.load(paths["rec_ev_002_bias"], allow_pickle=False)
    items = np.load(paths["cohort_excluded_item_factors"], allow_pickle=False)
    users = np.load(paths["foldin_user_factors"], allow_pickle=False)
    onboarding = pd.read_parquet(paths["onboarding_first_20"], columns=["user_id", "movie_id", "rating", "onboarding_order"])
    return {"cold": cold, "dual": dual, "baseline": baseline, "paths": paths, "positives": positives,
            "selection": selection, "bias": bias, "baseline_bias": baseline_bias,
            "items": items, "users": users, "onboarding": onboarding}


def protocol(args: argparse.Namespace, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": PROTOCOL,
        "sources": {"rec_ev_003_manifest_sha256": sha256(args.cold_start_manifest),
                    "rec_ev_003b_manifest_sha256": sha256(args.dual_head_manifest),
                    "rec_ev_002_manifest_sha256": sha256(args.baseline_manifest),
                    **{f"{name}_sha256": sha256(path) for name, path in source["paths"].items()}},
        "cohort": {"split": "REC-EV-003B multiplicative-hash-parity-v1",
                   "selection_users": 1230, "evaluation_users": 1323},
        "k_values": list(K_VALUES), "alpha_grid": list(ALPHAS),
        "candidate_generation": {"universe": "all cohort-excluded Train-known movies",
                                 "exclude": "first K onboarding-seen movies",
                                 "positive_injection": False, "score_scan": "all eligible movies",
                                 "top_candidates": TOP_CANDIDATES, "top_k": TOP_K,
                                 "tie_break": "score descending then movieId ascending"},
        "fold_in": "REC-EV-003 leakage-safe cohort-excluded retraining of REC-EV-002 ALS configuration and precomputed K user factors; bias fallback for items without cohort-excluded factor",
        "blend": "(1-alpha)*Bayesian Popularity + alpha*Fold-in-or-bias-fallback",
        "sampled_prior_result": "REC-EV-003B selected alpha=0 for K1/3/5/10/20",
        "ranking_champion": None, "expected_star_or_public_ui_approved": False,
    }


def rank_of_positive(universe: np.ndarray, scores: np.ndarray, positive: int) -> int | None:
    position = np.searchsorted(universe, positive)
    if position >= len(universe) or int(universe[position]) != positive or not np.isfinite(scores[position]):
        return None
    value = scores[position]
    return 1 + int(np.count_nonzero(scores > value)) + int(np.count_nonzero((scores == value) & (universe < positive)))


def prepare_arrays(source: dict[str, Any]) -> dict[str, Any]:
    bias, item_data, user_data = source["bias"], source["items"], source["users"]
    baseline_bias = source["baseline_bias"]
    movie_counts = baseline_bias["movie_counts"].astype(np.int64, copy=False)
    universe = np.flatnonzero(movie_counts > 0).astype(np.int64)
    if len(universe) != 50_977:
        raise RuntimeError(f"REC-EV-002 Train-known universe is {len(universe)}, expected 50977")
    popularity = predict_popularity(universe, float(baseline_bias["global_mean"]), movie_counts,
                                    baseline_bias["movie_sums"].astype(np.float64, copy=False), prior=50.0)
    dense_factors = np.full((len(movie_counts), item_data["movie_factors"].shape[1]), np.nan, dtype=np.float32)
    dense_factors[item_data["movie_ids"].astype(np.int64)] = item_data["movie_factors"]
    movie_bias = bias["movie_bias"].astype(np.float64, copy=False)
    user_ids = user_data["user_ids"].astype(np.int64)
    if tuple(int(v) for v in user_data["k_values"]) != (0, *K_VALUES):
        raise RuntimeError("fold-in K axis mismatch")
    onboarding = source["onboarding"]
    seen = {k: {int(user): group.loc[group["onboarding_order"] <= k, "movie_id"].to_numpy(dtype=np.int64)
                for user, group in onboarding.groupby("user_id", sort=False)} for k in K_VALUES}
    user_biases = {k: onboarding_user_bias(user_ids, onboarding, k, float(bias["global_mean"]), movie_bias, 10.0)
                   for k in K_VALUES}
    return {"universe": universe, "popularity": popularity, "dense_factors": dense_factors,
            "movie_bias": movie_bias, "global_mean": float(bias["global_mean"]), "user_ids": user_ids,
            "factor_cube": user_data["user_factors"], "seen": seen, "user_biases": user_biases}


def user_scores(arrays: dict[str, Any], user: int, k: int) -> tuple[np.ndarray, np.ndarray]:
    universe = arrays["universe"]
    position = int(np.searchsorted(arrays["user_ids"], user))
    factor = arrays["factor_cube"][list((0, *K_VALUES)).index(k), position].astype(np.float64)
    item_factors = arrays["dense_factors"][universe]
    direct = item_factors @ factor
    fallback = arrays["global_mean"] + arrays["user_biases"][k][position] + arrays["movie_bias"][universe]
    fold = np.where(np.isfinite(direct), direct, fallback)
    pop = arrays["popularity"].copy()
    seen_positions = np.searchsorted(universe, arrays["seen"][k][user])
    valid = (seen_positions < len(universe)) & (universe[np.minimum(seen_positions, len(universe) - 1)] == arrays["seen"][k][user])
    seen_positions = seen_positions[valid]
    pop[seen_positions] = -np.inf
    fold[seen_positions] = -np.inf
    return pop, fold


def blend_scores(pop: np.ndarray, fold: np.ndarray, alpha: float) -> np.ndarray:
    if alpha == 0.0:
        return pop.copy()
    if alpha == 1.0:
        return fold.copy()
    result = (1.0 - alpha) * pop + alpha * fold
    result[~np.isfinite(pop)] = -np.inf
    return result


def per_user_record(universe: np.ndarray, scores: np.ndarray, positive: int) -> tuple[dict[str, Any], np.ndarray]:
    rank = rank_of_positive(universe, scores, positive)
    top = deterministic_top_k(universe, scores, TOP_K)
    return {"candidate_hit": float(rank is not None and rank <= TOP_CANDIDATES),
            "recall": float(rank is not None and rank <= TOP_K),
            "ndcg": 1.0 / math.log2(rank + 1) if rank is not None and rank <= TOP_K else 0.0,
            "rank": rank if rank is not None else len(universe) + 1}, top


def summarize(rows: list[dict[str, Any]], unique: set[int], universe_size: int) -> dict[str, Any]:
    frame = pd.DataFrame(rows)
    return {"users": len(frame), "candidate_recall_at_500": round(float(frame["candidate_hit"].mean()), 6),
            "ndcg_at_10": round(float(frame["ndcg"].mean()), 6), "recall_at_10": round(float(frame["recall"].mean()), 6),
            "catalog_coverage": round(len(unique) / universe_size, 6)}


def run_selection(args: argparse.Namespace) -> None:
    started = time.perf_counter(); process = psutil.Process(); peak = process.memory_info().rss
    source = load_sources(args); arrays = prepare_arrays(source)
    positives = source["positives"].loc[source["selection"]]
    grids: dict[str, Any] = {}; selected: dict[str, float] = {}
    for k in K_VALUES:
        accum = {alpha: [] for alpha in ALPHAS}
        for row in positives.itertuples(index=False):
            pop, fold = user_scores(arrays, int(row.user_id), k)
            for alpha in ALPHAS:
                rank = rank_of_positive(arrays["universe"], blend_scores(pop, fold, alpha), int(row.movie_id))
                accum[alpha].append(1.0 / math.log2(rank + 1) if rank is not None and rank <= TOP_K else 0.0)
        grid = [{"alpha": alpha, "ndcg_at_10": round(float(np.mean(accum[alpha])), 6)} for alpha in ALPHAS]
        chosen = max(grid, key=lambda value: (value["ndcg_at_10"], -value["alpha"]))["alpha"]
        grids[str(k)] = grid; selected[str(k)] = chosen; peak = max(peak, process.memory_info().rss)
    result = {"schema_version": 1, "evidence_id": "REC-EV-011", "phase": "SELECTION",
              "protocol": PROTOCOL, "users": len(positives), "alpha_grid_metrics": grids, "selected_alpha": selected,
              "sampled_rec_ev_003b_alpha": {str(k): 0.0 for k in K_VALUES},
              "resources": {"elapsed_seconds": round(time.perf_counter()-started,3), "peak_rss_bytes_observed": peak,
                            "full_score_evaluations": len(positives)*len(arrays["universe"])*len(K_VALUES)*len(ALPHAS)}}
    write_json(args.selection_result, result)
    locked = protocol(args, source)
    lock = {"schema_version": 1, "evidence_id": "REC-EV-011", "phase_gate": "SELECTION_LOCKED_EVALUATION_CLOSED",
            "protocol": locked, "protocol_hash": hashlib.sha256(canonical_bytes(locked)).hexdigest(),
            "selection_result": artifact(args.selection_result), "selected_alpha": selected}
    write_json(args.protocol_lock, lock)
    print(json.dumps({"status":"PASS","phase":"SELECTION","selected_alpha":selected,"protocol_hash":lock["protocol_hash"]}))


def paired_ci(base: list[dict[str, Any]], candidate: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    diff = np.asarray([c["ndcg"] - b["ndcg"] for b, c in zip(base, candidate, strict=True)])
    rng = np.random.default_rng(seed); boot = np.asarray([np.mean(rng.choice(diff, len(diff), replace=True)) for _ in range(1000)])
    return {"users": len(diff), "mean_difference": round(float(diff.mean()),6),
            "ci95_low": round(float(np.quantile(boot,.025)),6), "ci95_high": round(float(np.quantile(boot,.975)),6),
            "bootstrap_repeats":1000}


def run_evaluation(args: argparse.Namespace) -> None:
    lock=json.loads(args.protocol_lock.read_text(encoding="utf-8")); selection=exact_artifact(lock["selection_result"])
    if selection.resolve()!=args.selection_result.resolve() or hashlib.sha256(canonical_bytes(lock["protocol"])).hexdigest()!=lock["protocol_hash"]:
        raise RuntimeError("selection lock mismatch")
    source=load_sources(args)
    if protocol(args,source)!=lock["protocol"]: raise RuntimeError("current source differs from lock")
    started=time.perf_counter(); process=psutil.Process(); peak=process.memory_info().rss; arrays=prepare_arrays(source)
    positives=source["positives"].loc[~source["selection"]]
    metrics={}; cis={}; segments={}; failures={}
    for k in K_VALUES:
        base_rows=[]; chosen_rows=[]; base_unique=set(); chosen_unique=set(); alpha=float(lock["selected_alpha"][str(k)])
        histories=[]
        for row in positives.itertuples(index=False):
            pop,fold=user_scores(arrays,int(row.user_id),k)
            base,base_top=per_user_record(arrays["universe"],pop,int(row.movie_id))
            chosen,chosen_top=per_user_record(arrays["universe"],blend_scores(pop,fold,alpha),int(row.movie_id))
            base_rows.append(base); chosen_rows.append(chosen); base_unique.update(base_top.tolist()); chosen_unique.update(chosen_top.tolist())
            histories.append(len(source["onboarding"].loc[source["onboarding"]["user_id"]==int(row.user_id)]))
        metrics[str(k)]={"selected_alpha":alpha,"POPULARITY":summarize(base_rows,base_unique,len(arrays["universe"])),
                         "FOLDIN_BLEND":summarize(chosen_rows,chosen_unique,len(arrays["universe"]))}
        cis[str(k)]=paired_ci(base_rows,chosen_rows,SEED+100+k)
        failures[str(k)]=[{"case":i+1,"popularity_rank":base_rows[idx]["rank"],"foldin_blend_rank":chosen_rows[idx]["rank"]}
                          for i,idx in enumerate(np.argsort([c["rank"]-b["rank"] for b,c in zip(base_rows,chosen_rows,strict=True)])[-5:][::-1])]
        segments[str(k)]={"evaluation_users":len(positives),"factor_user_coverage":round(float(np.mean(source["users"]["available_item_counts"][list((0,*K_VALUES)).index(k)]>0)),6)}
        peak=max(peak,process.memory_info().rss)
    offline_candidate = None if all(value["selected_alpha"]==0.0 for value in metrics.values()) else "K10_FULL_CATALOG_OFFLINE_CANDIDATE"
    result={"schema_version":1,"evidence_id":"REC-EV-011","phase":"EVALUATION","protocol_hash":lock["protocol_hash"],
            "coverage":{"full_catalog":True,"train_known_movies":len(arrays["universe"]),"users":len(positives),"positive_injection":False},
            "metrics":metrics,"paired_ndcg_vs_popularity":cis,"segments":segments,"failure_cases":failures,
            "resources":{"elapsed_seconds":round(time.perf_counter()-started,3),"peak_rss_bytes_observed":peak,
                         "full_score_evaluations":len(positives)*len(arrays["universe"])*len(K_VALUES)*2},
            "conclusion":{"personal_ranking_champion":None,"offline_candidate":offline_candidate,"expected_star_approved":False,"public_ui_approved":False,
                          "sampled_alpha_zero_disclosed":True}}
    write_json(args.evaluation_result,result)
    manifest={"schema_version":1,"evidence_id":"REC-EV-011","protocol":lock["protocol"],"selected_alpha":lock["selected_alpha"],
              "artifacts":{"protocol_lock":artifact(args.protocol_lock),"selection_result":artifact(args.selection_result),"evaluation_result":artifact(args.evaluation_result)},
              "validation":{"status":"PASS","selection_lock_verified_before_evaluation":True,"positive_injection":False,"raw_ids_tracked":False},
              "conclusion":result["conclusion"]}
    write_json(args.manifest,manifest)
    print(json.dumps({"status":"PASS","phase":"EVALUATION","selected_alpha":lock["selected_alpha"],"champion":None,"offline_candidate":offline_candidate}))


def parse_args() -> argparse.Namespace:
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest="phase",required=True)
    for name in ("selection","evaluation"):
        p=sub.add_parser(name); p.add_argument("--cold-start-manifest",type=Path,required=True); p.add_argument("--dual-head-manifest",type=Path,required=True); p.add_argument("--baseline-manifest",type=Path,required=True)
        p.add_argument("--selection-result",type=Path,required=True); p.add_argument("--protocol-lock",type=Path,required=True)
        if name=="evaluation": p.add_argument("--evaluation-result",type=Path,required=True); p.add_argument("--manifest",type=Path,required=True)
    return parser.parse_args()


if __name__=="__main__":
    args=parse_args(); run_selection(args) if args.phase=="selection" else run_evaluation(args)
