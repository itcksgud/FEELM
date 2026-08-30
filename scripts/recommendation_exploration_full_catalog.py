from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psutil
from scipy.stats import rankdata

from recommendation_baseline_calibration import (
    build_profile_count_matrix,
    predict_popularity,
    rating_midrank_ecdf,
    safe_take,
)
from recommendation_exploration_pareto import (
    js_distance,
    load_genres,
    paired_ndcg_difference,
    pareto_front,
    user_genre_profiles,
)

PROTOCOL = "rec-ev-004b-full-catalog-v1"
POLICIES = ("POPULARITY", "CONTENT_GENRE", "HYBRID_CONTENT_25", "EXPLORE_05_ON_POPULARITY")
TOP_CANDIDATES = 500
TOP_K = 10
SEED = 42


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def exact_artifact(record: dict[str, Any]) -> Path:
    path = Path(record["path"])
    if not path.is_file() or path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
        raise RuntimeError("source artifact checksum mismatch")
    return path


def deterministic_top_k(movie_ids: np.ndarray, scores: np.ndarray, k: int) -> np.ndarray:
    """Highest finite scores, with ascending movie ID as the exact tie-break."""
    valid = np.flatnonzero(np.isfinite(scores))
    if len(valid) == 0:
        return np.empty(0, dtype=np.int64)
    k = min(k, len(valid))
    values = scores[valid]
    threshold = np.partition(values, len(values) - k)[len(values) - k]
    greater = valid[values > threshold]
    equal = valid[values == threshold]
    remaining = k - len(greater)
    equal = equal[np.argsort(movie_ids[equal], kind="stable")[:remaining]]
    selected = np.concatenate((greater, equal))
    order = np.lexsort((movie_ids[selected], -scores[selected]))
    return movie_ids[selected[order]].astype(np.int64, copy=False)


def select_warm_positives(
    heldout: pd.DataFrame,
    user_counts: np.ndarray,
    movie_counts: np.ndarray,
    profile_matrix: np.ndarray,
    profile_totals: np.ndarray,
) -> pd.DataFrame:
    users = heldout["user_id"].to_numpy(dtype=np.int64)
    movies = heldout["movie_id"].to_numpy(dtype=np.int64)
    known = (safe_take(user_counts, users) > 0) & (safe_take(movie_counts, movies) > 0)
    utility = np.full(len(heldout), np.nan, dtype=np.float64)
    utility[known] = rating_midrank_ecdf(
        heldout.loc[known, "rating"].to_numpy(dtype=np.float64), users[known],
        profile_matrix, profile_totals, profile_matrix.sum(axis=0), shrinkage=20.0,
    )
    warm = known & (utility >= 0.7)
    positives = heldout.loc[warm, ["user_id", "movie_id", "timestamp"]].copy()
    positives = positives.sort_values(["user_id", "timestamp", "movie_id"], kind="stable")
    positives = positives.groupby("user_id", sort=True, as_index=False).tail(1)
    if positives.empty:
        raise RuntimeError("no warm held-out positives")
    return positives.sort_values("user_id", kind="stable").reset_index(drop=True)


def build_seen(train: pd.DataFrame, users: np.ndarray) -> dict[int, np.ndarray]:
    relevant = train.loc[train["user_id"].isin(users), ["user_id", "movie_id"]]
    return {
        int(user): np.unique(group["movie_id"].to_numpy(dtype=np.int64))
        for user, group in relevant.groupby("user_id", sort=True)
    }


def explore_top10(
    candidate_movies: np.ndarray,
    universe_positions: np.ndarray,
    pop_pct: np.ndarray,
    novelty_pct: np.ndarray,
    genre_matrix: np.ndarray,
    genre_available: np.ndarray,
) -> np.ndarray:
    remaining = np.arange(len(candidate_movies), dtype=np.int64)
    selected: list[int] = []
    while len(selected) < TOP_K and len(remaining):
        selected_movies = candidate_movies[np.asarray(selected, dtype=np.int64)]
        selected_known = selected_movies[genre_available[selected_movies]]
        candidate_known = genre_available[candidate_movies[remaining]]
        if not len(selected_known):
            diversity = np.zeros(len(remaining), dtype=np.float64)
        else:
            similarities = genre_matrix[selected_known] @ genre_matrix[candidate_movies[remaining]].T
            diversity = np.where(candidate_known, 1.0 - np.max(similarities, axis=0), 0.0)
        positions = universe_positions[remaining]
        score = 0.95 * pop_pct[positions] + 0.05 * (0.5 * novelty_pct[positions] + 0.5 * diversity)
        choice = int(np.lexsort((candidate_movies[remaining], -score))[0])
        selected.append(int(remaining[choice]))
        remaining = np.delete(remaining, choice)
    return candidate_movies[np.asarray(selected, dtype=np.int64)]


def conservative_genre_diversity(movie_ids: np.ndarray, genre_matrix: np.ndarray, genre_available: np.ndarray) -> tuple[float, float]:
    """Unknown-genre pairs contribute zero diversity and are reported as uncovered."""
    if len(movie_ids) < 2:
        return 0.0, 0.0
    values = genre_matrix[movie_ids]
    similarities = values @ values.T
    left, right = np.triu_indices(len(movie_ids), 1)
    pair_known = genre_available[movie_ids[left]] & genre_available[movie_ids[right]]
    pair_diversity = np.where(pair_known, 1.0 - similarities[left, right], 0.0)
    return float(np.mean(pair_diversity)), float(np.mean(pair_known))


def evaluate_phase(
    train: pd.DataFrame,
    heldout: pd.DataFrame,
    bias: Any,
    genre_matrix: np.ndarray,
    genre_available: np.ndarray,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame], dict[str, Any]]:
    started = time.perf_counter()
    process = psutil.Process()
    peak_rss = process.memory_info().rss
    movie_counts = bias["movie_counts"].astype(np.int64, copy=False)
    movie_sums = bias["movie_sums"].astype(np.float64, copy=False)
    user_counts = bias["user_counts"].astype(np.int64, copy=False)
    global_mean = float(bias["global_mean"])
    train_users = train["user_id"].to_numpy(dtype=np.int64)
    train_ratings = train["rating"].to_numpy(dtype=np.float64)
    profiles_matrix, profile_totals = build_profile_count_matrix(train_users, train_ratings, len(user_counts))
    positives = select_warm_positives(heldout, user_counts, movie_counts, profiles_matrix, profile_totals)
    users = positives["user_id"].to_numpy(dtype=np.int64)
    profiles, exposure = user_genre_profiles(train, users, genre_matrix)
    seen = build_seen(train, users)
    universe = np.flatnonzero(movie_counts > 0).astype(np.int64)
    if len(universe) != 50_977:
        raise RuntimeError(f"Train-known universe is {len(universe)}, expected 50977")
    dense_position = np.full(len(movie_counts), -1, dtype=np.int64)
    dense_position[universe] = np.arange(len(universe), dtype=np.int64)
    popularity = predict_popularity(universe, global_mean, movie_counts, movie_sums, prior=50.0)
    novelty = -np.log2((movie_counts[universe] + 1.0) / (movie_counts.sum() + len(universe)))
    pop_pct = rankdata(popularity, method="average") / len(universe)
    novelty_pct = rankdata(novelty, method="average") / len(universe)
    head_count = max(1, int(math.ceil(len(universe) * 0.2)))
    head = set(universe[np.argsort(movie_counts[universe], kind="stable")[-head_count:]].tolist())
    profile_positions = {int(user): index for index, user in enumerate(users)}
    positives_map = {int(row.user_id): int(row.movie_id) for row in positives.itertuples(index=False)}
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in POLICIES}
    unique: dict[str, set[int]] = {name: set() for name in POLICIES}

    for number, user in enumerate(users, 1):
        user = int(user)
        content = genre_matrix[universe] @ profiles[profile_positions[user]]
        content_pct = rankdata(content, method="average") / len(universe)
        scores = {
            "POPULARITY": pop_pct.copy(),
            "CONTENT_GENRE": content_pct,
            "HYBRID_CONTENT_25": 0.75 * pop_pct + 0.25 * content_pct,
        }
        seen_positions = dense_position[seen[user]]
        seen_positions = seen_positions[seen_positions >= 0]
        for value in scores.values():
            value[seen_positions] = -np.inf
        candidate_sets = {
            name: deterministic_top_k(universe, score, TOP_CANDIDATES) for name, score in scores.items()
        }
        pop_candidates = candidate_sets["POPULARITY"]
        pop_candidate_positions = dense_position[pop_candidates]
        top10 = {
            "POPULARITY": pop_candidates[:TOP_K],
            "CONTENT_GENRE": candidate_sets["CONTENT_GENRE"][:TOP_K],
            "HYBRID_CONTENT_25": candidate_sets["HYBRID_CONTENT_25"][:TOP_K],
            "EXPLORE_05_ON_POPULARITY": explore_top10(
                pop_candidates, pop_candidate_positions, pop_pct, novelty_pct, genre_matrix, genre_available
            ),
        }
        candidate_sets["EXPLORE_05_ON_POPULARITY"] = pop_candidates
        positive = positives_map[user]
        for policy in POLICIES:
            candidates = candidate_sets[policy]
            result = top10[policy]
            candidate_hits = np.flatnonzero(candidates == positive)
            top_hits = np.flatnonzero(result == positive)
            rank = int(top_hits[0] + 1) if len(top_hits) else None
            candidate_rank = int(candidate_hits[0] + 1) if len(candidate_hits) else None
            unique[policy].update(result.tolist())
            list_profile = genre_matrix[result].sum(axis=0)
            calibration = js_distance(exposure[profile_positions[user]], list_profile)
            conservative_diversity, pair_genre_coverage = conservative_genre_diversity(
                result, genre_matrix, genre_available
            )
            rows[policy].append({
                "user_id": user,
                "rank": rank if rank is not None else TOP_CANDIDATES + 1,
                "candidate_rank": candidate_rank if candidate_rank is not None else TOP_CANDIDATES + 1,
                "candidate_hit": int(candidate_rank is not None),
                "ndcg": 1.0 / math.log2(rank + 1) if rank is not None else 0.0,
                "recall": float(rank is not None),
                "novelty": float(np.mean(novelty[dense_position[result]])),
                "diversity": conservative_diversity,
                "list_genre_coverage": float(np.mean(genre_available[result])),
                "pair_genre_coverage": pair_genre_coverage,
                "long_tail": float(np.mean([int(movie) not in head for movie in result])),
                "calibration_distance": calibration,
            })
        if number % 100 == 0:
            peak_rss = max(peak_rss, process.memory_info().rss)

    per_user = {name: pd.DataFrame(value) for name, value in rows.items()}
    metrics: dict[str, Any] = {}
    for name, frame in per_user.items():
        metrics[name] = {
            "users": len(frame),
            "candidate_recall_at_500": round(float(frame["candidate_hit"].mean()), 6),
            "ndcg_at_10": round(float(frame["ndcg"].mean()), 6),
            "recall_at_10": round(float(frame["recall"].mean()), 6),
            "novelty_bits": round(float(frame["novelty"].mean()), 6),
            "intra_list_diversity": round(float(frame["diversity"].mean()), 6),
            "list_genre_coverage": round(float(frame["list_genre_coverage"].mean()), 6),
            "pair_genre_coverage": round(float(frame["pair_genre_coverage"].mean()), 6),
            "catalog_coverage": round(len(unique[name]) / len(universe), 6),
            "long_tail_exposure": round(float(frame["long_tail"].mean()), 6),
            "genre_calibration_distance": round(float(frame["calibration_distance"].dropna().mean()), 6),
            "genre_calibration_coverage": round(float(frame["calibration_distance"].notna().mean()), 6),
        }
    resources = {
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "peak_rss_bytes_observed": int(max(peak_rss, process.memory_info().rss)),
        "python": platform.python_version(),
        "users_completed": len(users),
        "full_score_evaluations": int(len(users) * len(universe)),
    }
    context = {
        "users": users,
        "positives": positives_map,
        "train_counts": train.groupby("user_id").size().astype(int).to_dict(),
        "movie_counts": movie_counts,
        "universe": universe,
    }
    return metrics, per_user, {"resources": resources, "context": context}


def segments(frame: pd.DataFrame, users: np.ndarray, positives: dict[int, int], train_counts: dict[int, int], movie_counts: np.ndarray) -> dict[str, Any]:
    value = frame.copy()
    value["history"] = value["user_id"].map(lambda user: train_counts[int(user)])
    value["history_segment"] = pd.cut(value["history"], [0, 49, 99, np.inf], labels=["K20_49", "K50_99", "K100_PLUS"])
    positive_counts = np.asarray([movie_counts[positives[int(user)]] for user in users], dtype=np.float64)
    q1, q2, q3 = np.quantile(positive_counts, [0.25, 0.5, 0.75])
    count_map = {int(user): float(count) for user, count in zip(users, positive_counts, strict=True)}
    value["positive_popularity_segment"] = value["user_id"].map(
        lambda user: "P1_LONG_TAIL" if count_map[int(user)] <= q1 else "P2" if count_map[int(user)] <= q2 else "P3" if count_map[int(user)] <= q3 else "P4_HEAD"
    )
    result: dict[str, Any] = {}
    for column in ("history_segment", "positive_popularity_segment"):
        result[column] = {}
        for label, group in value.groupby(column, observed=True):
            result[column][str(label)] = {
                "users": len(group), "candidate_recall_at_500": round(float(group["candidate_hit"].mean()), 6),
                "ndcg_at_10": round(float(group["ndcg"].mean()), 6), "recall_at_10": round(float(group["recall"].mean()), 6),
                "novelty_bits": round(float(group["novelty"].mean()), 6), "diversity": round(float(group["diversity"].mean()), 6),
                "list_genre_coverage": round(float(group["list_genre_coverage"].mean()), 6),
                "pair_genre_coverage": round(float(group["pair_genre_coverage"].mean()), 6),
                "long_tail_exposure": round(float(group["long_tail"].mean()), 6),
            }
    return result


def failures(baseline: pd.DataFrame, candidate: pd.DataFrame, train_counts: dict[int, int]) -> list[dict[str, Any]]:
    merged = baseline[["user_id", "rank", "candidate_rank"]].merge(
        candidate[["user_id", "rank", "candidate_rank"]], on="user_id", suffixes=("_pop", "_candidate"), validate="one_to_one"
    )
    merged["rank_regression"] = merged["rank_candidate"] - merged["rank_pop"]
    merged = merged.sort_values(["rank_regression", "rank_pop"], ascending=[False, True], kind="stable").head(5)
    return [{
        "case": index + 1,
        "history_segment": "K20_49" if train_counts[int(row.user_id)] < 50 else "K50_99" if train_counts[int(row.user_id)] < 100 else "K100_PLUS",
        "popularity_positive_rank_bounded_501": int(row.rank_pop),
        "candidate_positive_rank_bounded_501": int(row.rank_candidate),
        "rank_regression": int(row.rank_regression),
    } for index, row in enumerate(merged.itertuples(index=False))]


def load_common(args: argparse.Namespace, split_name: str) -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame, pd.DataFrame, Any, np.ndarray, np.ndarray, Path]:
    split_manifest = json.loads(args.split_manifest.read_text(encoding="utf-8"))
    baseline_manifest = json.loads(args.baseline_manifest.read_text(encoding="utf-8"))
    rec004 = json.loads(args.rec_ev_004_manifest.read_text(encoding="utf-8"))
    if split_manifest["protocol"]["version"] != "global-time-v1":
        raise RuntimeError("unexpected split protocol")
    if baseline_manifest["source"]["split_manifest_sha256"] != sha256(args.split_manifest):
        raise RuntimeError("REC-EV-002 is not bound to split manifest")
    if rec004.get("evidence_id") != "REC-EV-004" or set(POLICIES) - set(rec004["metrics"]["validation_metrics"]):
        raise RuntimeError("REC-EV-004 locked policy source mismatch")
    archive = args.archive or Path(split_manifest["source"]["archive"])
    if sha256(archive) != split_manifest["source"]["archive_sha256"]:
        raise RuntimeError("MovieLens archive checksum mismatch")
    train_path = exact_artifact(split_manifest["artifacts"]["train"])
    heldout_path = exact_artifact(split_manifest["artifacts"][split_name])
    train = pd.read_parquet(train_path, columns=["user_id", "movie_id", "rating", "timestamp"])
    heldout = pd.read_parquet(heldout_path, columns=["user_id", "movie_id", "rating", "timestamp"])
    bias_path = exact_artifact(baseline_manifest["artifacts"]["bias_parameters"])
    bias = np.load(bias_path, allow_pickle=False)
    if int(bias["movie_counts"].sum()) != len(train):
        raise RuntimeError("Train and REC-EV-002 sufficient statistics differ")
    genre_matrix, _, genre_available = load_genres(archive, len(bias["movie_counts"]))
    return split_manifest, baseline_manifest, train, heldout, bias, genre_matrix, genre_available, bias_path


def protocol_payload(args: argparse.Namespace, split_manifest: dict[str, Any], baseline_manifest: dict[str, Any], bias_path: Path) -> dict[str, Any]:
    return {
        "version": PROTOCOL,
        "source": {
            "split_manifest_sha256": sha256(args.split_manifest),
            "baseline_manifest_sha256": sha256(args.baseline_manifest),
            "rec_ev_004_manifest_sha256": sha256(args.rec_ev_004_manifest),
            "archive_sha256": split_manifest["source"]["archive_sha256"],
            "bias_parameters_sha256": sha256(bias_path),
        },
        "cohort": "same global-time-v1 warm latest held-out item with Train-only shrunk ECDF >= 0.7",
        "candidate_generation": {
            "universe": "all 50,977 Train-known movies",
            "exclude": "each user's Train-seen movies before Top-500",
            "positive_injection": False,
            "score_scan": "every eligible Train-known movie",
            "top_candidates": TOP_CANDIDATES,
            "top_k": TOP_K,
            "tie_break": "score descending then movieId ascending",
            "unknown_genre_policy": "selection diversity contribution=0; metric pair diversity=0; coverage reported separately",
        },
        "policies": {
            "POPULARITY": "REC-EV-002 Bayesian popularity prior=50",
            "CONTENT_GENRE": "Train centered-rating normalized genre profile cosine; full-universe midrank percentile",
            "HYBRID_CONTENT_25": "0.75 popularity full-universe midrank percentile + 0.25 content full-universe midrank percentile",
            "EXPLORE_05_ON_POPULARITY": "Popularity Top-500 then greedy 0.95 popularity + 0.05*(0.5 novelty percentile + 0.5 marginal genre diversity)",
        },
        "metrics": ["candidate_recall_at_500", "ndcg_at_10", "recall_at_10", "novelty_bits", "intra_list_diversity", "list_genre_coverage", "pair_genre_coverage", "catalog_coverage", "long_tail_exposure", "genre_calibration_distance"],
        "seed": SEED,
        "product_weight_approved": False,
        "exploration_2_plus_1_approved": False,
        "ranking_champion": None,
    }


def validation(args: argparse.Namespace) -> None:
    split, baseline, train, heldout, bias, genres, genre_available, bias_path = load_common(args, "validation")
    metrics, per_user, details = evaluate_phase(train, heldout, bias, genres, genre_available)
    paired = {name: paired_ndcg_difference(per_user["POPULARITY"], per_user[name], seed=SEED + index)
              for index, name in enumerate(POLICIES) if name != "POPULARITY"}
    aggregate = {
        "schema_version": 1, "evidence_id": "REC-EV-004B", "phase": "VALIDATION",
        "protocol": PROTOCOL, "coverage": {"full_catalog": True, "train_known_movies": 50_977,
        "users": details["resources"]["users_completed"], "positive_injection": False},
        "metrics": metrics, "paired_ndcg_vs_popularity": paired, "pareto_front": pareto_front(metrics),
        "resources": details["resources"], "selection": {"policies_locked_without_new_search": list(POLICIES),
        "product_weight": None, "ranking_champion": None},
    }
    write_json(args.validation_result, aggregate)
    protocol = protocol_payload(args, split, baseline, bias_path)
    lock = {
        "schema_version": 1, "evidence_id": "REC-EV-004B", "phase_gate": "VALIDATION_LOCKED_TEST_CLOSED",
        "protocol": protocol, "protocol_hash": hashlib.sha256(canonical_bytes(protocol)).hexdigest(),
        "validation_result": artifact(args.validation_result),
    }
    write_json(args.protocol_lock, lock)
    print(json.dumps({"status": "PASS", "phase": "VALIDATION", "users": details["resources"]["users_completed"], "protocol_hash": lock["protocol_hash"]}))


def evidence_markdown(manifest: dict[str, Any], result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    rows = "\n".join(
        f"| {name} | {value['candidate_recall_at_500']:.6f} | {value['ndcg_at_10']:.6f} | {value['recall_at_10']:.6f} | {value['novelty_bits']:.6f} | {value['intra_list_diversity']:.6f} | {value['list_genre_coverage']:.6f} | {value['pair_genre_coverage']:.6f} | {value['catalog_coverage']:.6f} | {value['long_tail_exposure']:.6f} |"
        for name, value in metrics.items()
    )
    return f"""# REC-EV-004B Full-catalog Hybrid·exploration Pareto revalidation

Status: `COMPLETED_FULL_CATALOG_EVIDENCE` (offline evidence only; no product policy approval)

## 결론

- `global-time-v1`의 REC-EV-004와 동일한 warm cohort에서 Train-known 영화 50,977개를 매 사용자마다 모두 score scan했다.
- 사용자의 Train-seen 영화는 Top-500 전에 제외했고 held-out positive는 후보에 강제 주입하지 않았다.
- Validation이 정책/프로토콜과 hash를 먼저 잠갔고 Test CLI는 그 hash와 Validation artifact checksum을 검증한 뒤에만 Test를 열었다.
- 이는 REC-EV-004의 sampled 결과를 재명명한 것이 아닌 별도 `REC-EV-004B` evidence다.
- `EXPLORE_05_ON_POPULARITY`는 Popularity와 같은 Top-500을 재정렬하므로 candidate recall@500은 0.3080으로 같지만, Test NDCG@10은 0.009382→0.005113(상대 손실 약 45.5%)이고 paired CI는 `[-0.006604,-0.002002]`다. Pareto 목록에 있다는 사실은 채택 근거가 아니다.
- 제품 탐험 weight, 2+1 구성, 개인화 ranking champion은 승인하지 않는다.

## Held-out Test ({result['coverage']['users']} users)

| Policy | candidate recall@500 | NDCG@10 | Recall@10 | Novelty bits | Diversity | List genre coverage | Pair genre coverage | Catalog coverage | Long-tail exposure |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{rows}

`candidate recall@500`은 사용자별 단일 held-out positive가 자연스럽게 Top-500에 들어온 비율이며 Top-10 relevance와 같은 지표가 아니다.

## 범위와 Gate

- Candidate: full 50,977 Train-known universe → seen exclusion → deterministic Top-500 → Top-10.
- genre 미상 zero-vector는 selection diversity 보상을 받지 않고, metric에서도 미상 genre가 포함된 pair를 diversity 0으로 보수 처리한다. list/pair genre coverage는 별도 보고한다.
- 비교 정책은 REC-EV-004에서 잠긴 `POPULARITY`, `CONTENT_GENRE`, `HYBRID_CONTENT_25`, `EXPLORE_05_ON_POPULARITY`뿐이다. 새 탐색은 하지 않았다.
- full catalog는 MovieLens Train-known 범위를 뜻하며 서비스 Catalog 전체나 production coverage를 뜻하지 않는다.
- tracked aggregate/lock은 raw user/movie ID를 포함하지 않는다. 실패 사례는 순번과 segment/rank만 남겼다.
- 측정 자원: {result['resources']['elapsed_seconds']:.3f}s, observed peak RSS {result['resources']['peak_rss_bytes_observed']} bytes, {result['resources']['full_score_evaluations']} score evaluations.

## 재현

```powershell
$env:PYTHONPATH=(Resolve-Path 'scripts').Path
py -3.12 scripts/recommendation_exploration_full_catalog.py validation --split-manifest docs/recommendation/evidence/manifests/global-time-v1.json --baseline-manifest docs/recommendation/evidence/manifests/rec-ev-002.json --rec-ev-004-manifest docs/recommendation/evidence/manifests/rec-ev-004.json --archive C:\\higher\\projects\\MM\\data\\raw\\ml-32m.zip --validation-result docs/recommendation/evidence/results/rec-ev-004b-validation.json --protocol-lock docs/recommendation/evidence/results/rec-ev-004b-protocol-lock.json
py -3.12 scripts/recommendation_exploration_full_catalog.py test --split-manifest docs/recommendation/evidence/manifests/global-time-v1.json --baseline-manifest docs/recommendation/evidence/manifests/rec-ev-002.json --rec-ev-004-manifest docs/recommendation/evidence/manifests/rec-ev-004.json --archive C:\\higher\\projects\\MM\\data\\raw\\ml-32m.zip --validation-result docs/recommendation/evidence/results/rec-ev-004b-validation.json --protocol-lock docs/recommendation/evidence/results/rec-ev-004b-protocol-lock.json --test-result docs/recommendation/evidence/results/rec-ev-004b-test.json --manifest docs/recommendation/evidence/manifests/rec-ev-004b.json --evidence docs/recommendation/evidence/REC-EV-004B-full-catalog-pareto.md
py -3.12 scripts/verify_recommendation_exploration_full_catalog.py --manifest docs/recommendation/evidence/manifests/rec-ev-004b.json
```
"""


def test(args: argparse.Namespace) -> None:
    lock = json.loads(args.protocol_lock.read_text(encoding="utf-8"))
    if lock.get("phase_gate") != "VALIDATION_LOCKED_TEST_CLOSED":
        raise RuntimeError("Validation lock is not closed")
    if hashlib.sha256(canonical_bytes(lock["protocol"])).hexdigest() != lock.get("protocol_hash"):
        raise RuntimeError("protocol lock hash mismatch")
    validation_record = lock["validation_result"]
    if Path(validation_record["path"]).resolve() != args.validation_result.resolve():
        raise RuntimeError("Validation result path differs from lock")
    exact_artifact(validation_record)
    validation_result = json.loads(args.validation_result.read_text(encoding="utf-8"))
    if validation_result.get("phase") != "VALIDATION" or validation_result["selection"]["policies_locked_without_new_search"] != list(POLICIES):
        raise RuntimeError("Validation selection artifact mismatch")
    # Rebind every current source hash before the Test artifact is resolved or read.
    preflight_split = json.loads(args.split_manifest.read_text(encoding="utf-8"))
    preflight_baseline = json.loads(args.baseline_manifest.read_text(encoding="utf-8"))
    preflight_bias_path = exact_artifact(preflight_baseline["artifacts"]["bias_parameters"])
    preflight_protocol = protocol_payload(args, preflight_split, preflight_baseline, preflight_bias_path)
    if preflight_protocol != lock["protocol"]:
        raise RuntimeError("current inputs differ from locked protocol")
    # The Test parquet is not resolved or read until every lock and source check above succeeds.
    split, baseline, train, heldout, bias, genres, genre_available, bias_path = load_common(args, "test")
    expected_protocol = protocol_payload(args, split, baseline, bias_path)
    if expected_protocol != lock["protocol"]:
        raise RuntimeError("current inputs differ from locked protocol")
    metrics, per_user, details = evaluate_phase(train, heldout, bias, genres, genre_available)
    paired = {name: paired_ndcg_difference(per_user["POPULARITY"], per_user[name], seed=SEED + 20_000 + index)
              for index, name in enumerate(POLICIES) if name != "POPULARITY"}
    context = details["context"]
    test_segments = {name: segments(frame, context["users"], context["positives"], context["train_counts"], context["movie_counts"])
                     for name, frame in per_user.items()}
    failure_cases = {name: failures(per_user["POPULARITY"], per_user[name], context["train_counts"])
                     for name in POLICIES if name != "POPULARITY"}
    result = {
        "schema_version": 1, "evidence_id": "REC-EV-004B", "phase": "TEST",
        "protocol": PROTOCOL, "protocol_hash": lock["protocol_hash"],
        "coverage": {"full_catalog": True, "train_known_movies": 50_977,
        "users": details["resources"]["users_completed"], "positive_injection": False,
        "score_scan_to_top_500_to_top_10": True},
        "metrics": metrics, "paired_ndcg_vs_popularity": paired, "segments": test_segments,
        "failure_cases": failure_cases, "pareto_front": pareto_front(metrics), "resources": details["resources"],
        "conclusion": {"product_weight": None, "exploration_2_plus_1": None, "ranking_champion": None,
        "scope": "MovieLens Train-known full catalog; not service production catalog coverage"},
    }
    write_json(args.test_result, result)
    manifest = {
        "schema_version": 1, "evidence_id": "REC-EV-004B", "protocol": lock["protocol"],
        "coverage": result["coverage"], "metrics": metrics,
        "artifacts": {"protocol_lock": artifact(args.protocol_lock), "validation_result": artifact(args.validation_result),
        "test_result": artifact(args.test_result)},
        "validation": {"status": "PASS", "validation_lock_verified_before_test_read": True,
        "full_catalog_score_scan": True, "train_seen_excluded": True, "positive_injection": False,
        "raw_ids_tracked": False, "same_warm_cohort_definition_as_rec_ev_004": True},
        "conclusion": result["conclusion"],
    }
    write_json(args.manifest, manifest)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(evidence_markdown(manifest, result), encoding="utf-8")
    print(json.dumps({"status": "PASS", "phase": "TEST", "users": details["resources"]["users_completed"], "manifest": str(args.manifest)}))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="REC-EV-004B two-phase full-catalog revalidation")
    sub = parser.add_subparsers(dest="phase", required=True)
    for name in ("validation", "test"):
        phase = sub.add_parser(name)
        phase.add_argument("--split-manifest", type=Path, required=True)
        phase.add_argument("--baseline-manifest", type=Path, required=True)
        phase.add_argument("--rec-ev-004-manifest", type=Path, required=True)
        phase.add_argument("--archive", type=Path)
        phase.add_argument("--validation-result", type=Path, required=True)
        phase.add_argument("--protocol-lock", type=Path, required=True)
        if name == "test":
            phase.add_argument("--test-result", type=Path, required=True)
            phase.add_argument("--manifest", type=Path, required=True)
            phase.add_argument("--evidence", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    validation(arguments) if arguments.phase == "validation" else test(arguments)
