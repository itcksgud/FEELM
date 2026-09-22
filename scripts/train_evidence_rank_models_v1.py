"""Retrospective, observed-candidate GBT/FM smoke comparison on evidence-v1 rows.

Not a service-ranking validation: metadata/public values are current snapshots and
MovieLens contains no exposures for unrated full-catalogue candidates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd
from scipy import sparse
import xgboost as xgb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_specific_features_v5 import FACTOR_FIELD_BUCKETS, build_sparse_content  # noqa: E402
from rating_semantics_v7 import utility  # noqa: E402


AXES = ("collection", "director", "cast", "country_genre", "keyword")
SEED = 625


def numeric_features(rows: pd.DataFrame) -> tuple[np.ndarray, list[str], dict[str, np.ndarray]]:
    """Ablatable source/relationship values; missingness remains explicit."""
    columns: dict[str, np.ndarray] = {}
    k = rows.k.to_numpy(np.float32)
    columns["history_log1p"] = np.log1p(k)
    public = []
    personal = []
    for axis in AXES:
        known = rows[f"{axis}_known"].fillna(False).to_numpy(np.float32)
        columns[f"{axis}_known"] = known
        personal.append(f"{axis}_known")
        for sign in ("pos", "strong_pos", "weak_pos", "neg"):
            name = f"{axis}_{sign}_log1p"
            columns[name] = np.log1p(rows[f"{axis}_{sign}_count"].fillna(0).to_numpy(np.float32))
            personal.append(name)
    tmdb_average = rows.tmdb_vote_average.notna().to_numpy(np.float32)
    tmdb_votes = rows.tmdb_vote_count.notna().to_numpy(np.float32)
    kobis = rows.kobis_audience_cumulative.notna().to_numpy(np.float32)
    for name, values in (
        ("tmdb_average_known", tmdb_average),
        ("tmdb_votes_known", tmdb_votes),
        ("tmdb_average_0_1", pd.to_numeric(rows.tmdb_vote_average, errors="coerce").fillna(0).to_numpy(np.float32) / 10),
        ("tmdb_votes_log1p", np.log1p(pd.to_numeric(rows.tmdb_vote_count, errors="coerce").fillna(0).to_numpy(np.float32))),
        ("kobis_known", kobis),
        ("kobis_audience_log1p", np.log1p(pd.to_numeric(rows.kobis_audience_cumulative, errors="coerce").fillna(0).to_numpy(np.float32))),
        ("kobis_verified", rows.kobis_verified.fillna(False).to_numpy(np.float32)),
    ):
        columns[name] = values
        public.append(name)
    # FM has a linear arm, so public risk can alter a supported personal
    # relation only if the cross is explicit. These continuous crosses let the
    # fit learn the direction; no 3,000-vote or national-origin cutoff is used.
    vote_strength = columns["tmdb_votes_log1p"] / 14.0
    kobis_strength = columns["kobis_audience_log1p"] / 20.0
    neither_source = (1 - tmdb_votes) * (1 - kobis)
    cross = []
    for axis in AXES:
        for sign in ("pos", "neg"):
            base = columns[f"{axis}_{sign}_log1p"]
            for source, value in (("tmdb", vote_strength), ("kobis", kobis_strength),
                                  ("no_public", neither_source)):
                name = f"{axis}_{sign}_x_{source}"
                columns[name] = base * value
                cross.append(name)
    names = list(columns)
    matrix = np.column_stack(list(columns.values())).astype(np.float32)
    if not np.isfinite(matrix).all():
        raise ValueError("numeric features must be finite")
    masks = {
        "public_only": np.asarray([name not in personal and name not in cross for name in names], bool),
        "personal_only": np.asarray([name not in public and name not in cross for name in names], bool),
        "combined": np.ones(len(names), bool),
    }
    return matrix, names, masks


def _ordered(rows: pd.DataFrame) -> pd.DataFrame:
    return rows.sort_values(["qid", "movie_id"], kind="stable").reset_index(drop=True)


def _groups(qid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    boundary = np.r_[0, np.flatnonzero(qid[1:] != qid[:-1]) + 1, len(qid)]
    return np.diff(boundary), boundary


def _gain(rating: np.ndarray) -> np.ndarray:
    """3 stars has positive but weaker gain; <=2.5 has no gain."""
    return np.select(
        [rating >= 5, rating >= 4.5, rating >= 4, rating >= 3.5, rating >= 3],
        [5, 4, 3, 2, 1], default=0,
    ).astype(np.float32)


def evaluate(rows: pd.DataFrame, prediction: np.ndarray) -> dict:
    if len(rows) != len(prediction):
        raise ValueError("one prediction per observed candidate required")
    qid = rows.qid.to_numpy(np.int64)
    _, boundaries = _groups(qid)
    rating = rows.rating.to_numpy(np.float32)
    gains = _gain(rating)
    per_query = []
    for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
        if stop - start < 2:
            continue
        y, score = gains[start:stop], prediction[start:stop]
        order = np.argsort(-score, kind="stable")[:10]
        ideal = np.argsort(-y, kind="stable")[:10]
        discount = 1 / np.log2(np.arange(2, len(order) + 2))
        dcg = float(np.sum((2 ** y[order] - 1) * discount))
        idcg = float(np.sum((2 ** y[ideal] - 1) * discount))
        better = y[:, None] > y[None, :]
        delta = score[:, None] - score[None, :]
        all_pairs = (float(((delta > 0) & better).sum() + .5 * ((delta == 0) & better).sum()) /
                     int(better.sum())) if better.any() else None
        per_query.append((int(rows.uid.iloc[start]), dcg / idcg if idcg else None, all_pairs))
    by_user: dict[int, list[tuple[float | None, float | None]]] = {}
    for uid, ndcg, auc in per_query:
        by_user.setdefault(uid, []).append((ndcg, auc))
    def macro(index):
        values = [float(np.mean([item[index] for item in group if item[index] is not None]))
                  for group in by_user.values() if any(item[index] is not None for item in group)]
        return float(np.mean(values)) if values else None
    return {"rows": len(rows), "queries": len(per_query), "users": len(by_user),
            "user_macro_observed_ndcg_at_10": macro(0),
            "user_macro_observed_all_grade_pairwise_auc": macro(1)}


def fit_gbt(train: pd.DataFrame, valid: pd.DataFrame, variant: str, output: Path,
            trees: int, threads: int) -> dict:
    train = _ordered(train)
    valid = _ordered(valid)
    xt, names, masks = numeric_features(train)
    xv, names_v, masks_v = numeric_features(valid)
    if names != names_v or not np.array_equal(masks[variant], masks_v[variant]):
        raise ValueError("train/valid feature contract differs")
    selected = masks[variant]
    train_groups, _ = _groups(train.qid.to_numpy(np.int64))
    valid_groups, _ = _groups(valid.qid.to_numpy(np.int64))
    if len(train_groups) == 0 or len(valid_groups) == 0:
        raise ValueError("both train and validation need ranking groups")
    model = xgb.XGBRanker(
        objective="rank:ndcg", eval_metric="ndcg@10", n_estimators=trees,
        learning_rate=.05, max_depth=4, min_child_weight=10, subsample=.8,
        colsample_bytree=.8, reg_lambda=2, tree_method="hist", random_state=SEED,
        n_jobs=threads,
    )
    model.fit(xt[:, selected], _gain(train.rating.to_numpy()), group=train_groups,
              eval_set=[(xv[:, selected], _gain(valid.rating.to_numpy()))],
              eval_group=[valid_groups], verbose=False)
    prediction = model.predict(xv[:, selected])
    model.save_model(output / f"gbt-{variant}.json")
    metrics = {"family": "GBT", "variant": variant, "trees": trees,
               "train_rows": len(train), "train_queries": len(train_groups),
               "features": [name for name, keep in zip(names, selected, strict=True) if keep],
               "validation": evaluate(valid, prediction)}
    return metrics


def _fm_matrices(rows: pd.DataFrame, contexts: pd.DataFrame, metadata: pd.DataFrame):
    """Build candidate/profile fields for exactly the same observed rows as GBT."""
    # A joint token distinguishes Japanese animation from Japanese live-action
    # even when the origin-country token is identical.
    decorated = metadata.copy()
    def tokens(value):
        if value is None or value is pd.NA or (isinstance(value, float) and np.isnan(value)):
            return []
        if isinstance(value, (list, tuple, set, np.ndarray)):
            return [str(item) for item in value if item is not None]
        return [str(value)]
    joint_values = []
    for row in decorated.itertuples(index=False):
        record = row._asdict()
        countries = tokens(record.get("origin_country_codes")) or tokens(record.get("production_country_codes"))
        genres = tokens(record.get("genre_ids"))
        joint_values.append([f"{country}|{genre}" for country in countries for genre in genres])
    decorated["joint_country_genre_tokens"] = joint_values
    content = build_sparse_content(decorated, fields=FACTOR_FIELD_BUCKETS +
                                   (("country_genre", "joint_country_genre_tokens", 2048),))
    movie_index = {int(movie): i for i, movie in enumerate(metadata.movie_id)}
    # An unmatched catalogue ID must not silently remove a difficult row from
    # FM's evaluation denominator; it receives an explicit zero content vector.
    unknown_index = content.shape[0]
    content = sparse.vstack([content, sparse.csr_matrix((1, content.shape[1]), dtype=np.float32)], format="csr")
    mapping = contexts.set_index("qid", verify_integrity=True)
    used_qids = rows.qid.drop_duplicates().to_numpy(np.int64)
    if not set(used_qids).issubset(mapping.index):
        raise ValueError("row qid missing context")
    profile_rows, profile_cols, profile_values = [], [], []
    for context_index, qid in enumerate(used_qids):
        ctx = mapping.loc[int(qid)]
        movies, stars = ctx.history_movie_ids, ctx.history_ratings
        if len(movies) != len(stars):
            raise ValueError("unaligned context history")
        weights = utility(np.asarray(stars, dtype=np.float32))
        weights = weights / max(float(np.abs(weights).sum()), 1.0)
        for movie, weight in zip(movies, weights, strict=True):
            if movie in movie_index and weight:
                profile_rows.append(context_index)
                profile_cols.append(movie_index[int(movie)])
                profile_values.append(float(weight))
    selector = sparse.csr_matrix((np.asarray(profile_values, np.float32),
                                  (profile_rows, profile_cols)),
                                 shape=(len(used_qids), content.shape[0]), dtype=np.float32)
    profiles = (selector @ content).tocsr()
    query_index = {int(qid): i for i, qid in enumerate(used_qids)}
    profile_row_index = np.fromiter((query_index[int(qid)] for qid in rows.qid),
                                    dtype=np.int64, count=len(rows))
    candidate_indices = np.fromiter((movie_index.get(int(movie), unknown_index) for movie in rows.movie_id),
                                    dtype=np.int64, count=len(rows))
    # Keep one sparse profile per query. Repeating it for every candidate would
    # make large-K training memory scale with rows × history length.
    return content[candidate_indices].tocsr(), profiles, profile_row_index


def _pairs(rows: pd.DataFrame, max_per_query: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    _, boundaries = _groups(rows.qid.to_numpy(np.int64))
    stars = rows.rating.to_numpy(np.float32)
    gains = _gain(stars)
    tiers = rows.evidence_tier.to_numpy(str) if "evidence_tier" in rows else np.repeat("UNKNOWN", len(rows))
    rng = np.random.default_rng(SEED)
    positive, negative, weights = [], [], []
    eligible_grades: dict[str, int] = {}
    sampled_grades: dict[str, int] = {}
    eligible_tiers: dict[str, int] = {}
    sampled_tiers: dict[str, int] = {}
    trainable_queries = 0
    for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
        high, low = np.nonzero(gains[start:stop, None] > gains[None, start:stop])
        if not len(high):
            continue
        trainable_queries += 1
        grade_keys = [f"{int(gains[start+h])}>{int(gains[start+l])}" for h, l in zip(high, low, strict=True)]
        tier_keys = [f"{tiers[start+h]}>{tiers[start+l]}" for h, l in zip(high, low, strict=True)]
        for key in grade_keys:
            eligible_grades[key] = eligible_grades.get(key, 0) + 1
        for key in tier_keys:
            eligible_tiers[key] = eligible_tiers.get(key, 0) + 1
        selected: set[int] = set()
        # One representative per graded contrast before filling the budget.
        # The fixed priority preserves 5>3, 4>3, 3>negative and 5>4 when present.
        priority = ("5>1", "3>1", "1>0", "5>3")
        grade_order = [*priority, *sorted(set(grade_keys) - set(priority))]
        for key in grade_order:
            possible = [i for i, value in enumerate(grade_keys) if value == key]
            if possible and len(selected) < max_per_query:
                selected.add(int(rng.choice(possible)))
        # Then cover relationship evidence contrasts if pair budget remains.
        for key in sorted(set(tier_keys)):
            possible = [i for i, value in enumerate(tier_keys) if value == key and i not in selected]
            if possible and len(selected) < max_per_query:
                selected.add(int(rng.choice(possible)))
        remaining = np.asarray([i for i in range(len(high)) if i not in selected], np.int64)
        capacity = max_per_query - len(selected)
        if capacity > 0 and len(remaining):
            selected.update(rng.choice(remaining, size=min(capacity, len(remaining)), replace=False).tolist())
        positions = np.asarray(sorted(selected), np.int64)
        for i in positions:
            grade_key, tier_key = grade_keys[i], tier_keys[i]
            sampled_grades[grade_key] = sampled_grades.get(grade_key, 0) + 1
            sampled_tiers[tier_key] = sampled_tiers.get(tier_key, 0) + 1
        p, n = high[positions] + start, low[positions] + start
        positive.extend(p.tolist())
        negative.extend(n.tolist())
        weights.extend(np.maximum(utility(stars[p]) - utility(stars[n]), .1).tolist())
    stats = {"trainable_queries": trainable_queries,
             "eligible_pairs": int(sum(eligible_grades.values())),
             "sampled_pairs": len(positive),
             "eligible_grade_pairs": eligible_grades, "sampled_grade_pairs": sampled_grades,
             "eligible_tier_pairs": eligible_tiers, "sampled_tier_pairs": sampled_tiers,
             "sampling_rule": "graded-contrast then tier-contrast representatives, remaining uniform without replacement"}
    return (np.asarray(positive, np.int64), np.asarray(negative, np.int64),
            np.asarray(weights, np.float32), stats)


def _fm_score(x: np.ndarray, candidate: sparse.csr_matrix, profile: sparse.csr_matrix,
              profile_index: np.ndarray,
              linear: np.ndarray, factors: np.ndarray, batch: int = 4096) -> np.ndarray:
    scores = np.empty(len(x), np.float32)
    for start in range(0, len(x), batch):
        stop = min(start + batch, len(x))
        c = np.asarray(candidate[start:stop] @ factors)
        p = np.asarray(profile[profile_index[start:stop]] @ factors)
        scores[start:stop] = x[start:stop] @ linear + np.sum(c * p, axis=1)
    return scores


def fit_fm(train: pd.DataFrame, valid: pd.DataFrame, contexts: pd.DataFrame,
           metadata: pd.DataFrame, variant: str, output: Path, epochs: int,
           batch_size: int, max_pairs_per_query: int, factors_count: int,
           fm_matrices=None) -> dict:
    train, valid = _ordered(train), _ordered(valid)
    train_x, names, masks = numeric_features(train)
    valid_x, names_v, masks_v = numeric_features(valid)
    if names != names_v or not np.array_equal(masks[variant], masks_v[variant]):
        raise ValueError("train/valid feature contract differs")
    selected = masks[variant]
    train_x, valid_x = train_x[:, selected], valid_x[:, selected]
    if fm_matrices is None:
        fm_matrices = (_fm_matrices(train, contexts, metadata),
                       _fm_matrices(valid, contexts, metadata))
    (train_c, train_p, train_pidx), (valid_c, valid_p, valid_pidx) = fm_matrices
    high, low, weight, pair_stats = _pairs(train, max_pairs_per_query)
    if not len(high):
        raise ValueError("FM needs unequal observed ratings within training queries")
    rng = np.random.default_rng(SEED)
    width = train_c.shape[1]
    factor = rng.normal(0, .01, (width, factors_count)).astype(np.float32)
    linear = np.zeros(train_x.shape[1], np.float32)
    factor_m = np.zeros_like(factor)
    factor_v = np.zeros_like(factor)
    linear_m = np.zeros_like(linear)
    linear_v = np.zeros_like(linear)
    best_score = -np.inf
    best = None
    trace = []
    step = 0
    interaction_on = variant != "public_only"
    for epoch in range(1, epochs + 1):
        order = rng.permutation(len(high))
        losses = []
        for offset in range(0, len(order), batch_size):
            take = order[offset:offset + batch_size]
            pos, neg, w = high[take], low[take], weight[take]
            diff_x = train_x[pos] - train_x[neg]
            if interaction_on:
                cp, pp = train_c[pos], train_p[train_pidx[pos]]
                cn, pn = train_c[neg], train_p[train_pidx[neg]]
                cp_f, pp_f = np.asarray(cp @ factor), np.asarray(pp @ factor)
                cn_f, pn_f = np.asarray(cn @ factor), np.asarray(pn @ factor)
                delta = diff_x @ linear + np.sum(cp_f * pp_f - cn_f * pn_f, axis=1)
            else:
                delta = diff_x @ linear
            probability = 1 / (1 + np.exp(np.clip(delta, -30, 30)))
            error = -(probability * w) / max(float(w.sum()), 1e-9)
            grad_linear = diff_x.T @ error + 2e-4 * linear
            if interaction_on:
                grad_factor = np.asarray(
                    cp.T @ (error[:, None] * pp_f) + pp.T @ (error[:, None] * cp_f)
                    - cn.T @ (error[:, None] * pn_f) - pn.T @ (error[:, None] * cn_f)
                ) + 2e-4 * factor
            step += 1
            linear_m = .9 * linear_m + .1 * grad_linear
            linear_v = .999 * linear_v + .001 * grad_linear ** 2
            linear -= .01 * (linear_m / (1 - .9 ** step)) / (np.sqrt(linear_v / (1 - .999 ** step)) + 1e-8)
            if interaction_on:
                factor_m = .9 * factor_m + .1 * grad_factor
                factor_v = .999 * factor_v + .001 * grad_factor ** 2
                factor -= .01 * (factor_m / (1 - .9 ** step)) / (np.sqrt(factor_v / (1 - .999 ** step)) + 1e-8)
            losses.append(float(np.average(np.logaddexp(0, -delta), weights=w)))
        prediction = _fm_score(valid_x, valid_c, valid_p, valid_pidx, linear, factor)
        if not interaction_on:
            prediction = valid_x @ linear
        metrics = evaluate(valid, prediction)
        score = metrics["user_macro_observed_ndcg_at_10"]
        trace.append({"epoch": epoch, "train_pairwise_loss": float(np.mean(losses)), **metrics})
        if score is not None and score > best_score:
            best_score, best = score, (linear.copy(), factor.copy(), metrics)
    if best is None:
        raise ValueError("FM validation has no graded groups")
    linear, factor, metrics = best
    np.savez_compressed(output / f"fm-{variant}.npz", linear=linear, factor=factor,
                        feature_names=np.asarray(names)[selected], variant=variant)
    return {"family": "FM", "variant": variant, "epochs": epochs,
            "train_rows_available": len(train), "train_queries_available": int(train.qid.nunique()),
            "train_pairs": len(high), "max_pairs_per_query": max_pairs_per_query,
            "training_pair_support": pair_stats,
            "factor_width": width, "factor_count": factors_count,
            "validation": metrics, "trace": trace}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trees", type=int, default=60)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--max-pairs-per-query", type=int, default=12)
    parser.add_argument("--factors", type=int, default=8)
    parser.add_argument("--family", choices=("gbt", "fm", "both"), default="both")
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {args.output_dir}")
    rows_path = args.dataset / "rows.parquet"
    contexts_path = args.dataset / "contexts.parquet"
    manifest = json.loads((args.dataset / "manifest.json").read_text(encoding="utf-8"))
    def digest(path):
        h = hashlib.sha256()
        with path.open("rb") as source:
            for part in iter(lambda: source.read(8 * 1024 * 1024), b""):
                h.update(part)
        return h.hexdigest()
    for name, path in (("rows", rows_path), ("contexts", contexts_path)):
        if digest(path) != manifest["artifacts"][name]["sha256"]:
            raise ValueError(f"dataset artifact hash mismatch: {name}")
    rows = pd.read_parquet(rows_path)
    contexts = pd.read_parquet(contexts_path)
    train = rows[rows.split == "train"].copy()
    valid = rows[rows.split == "valid"].copy()
    if train.empty or valid.empty:
        raise ValueError("train and validation splits must be nonempty")
    if set(train.uid) & set(valid.uid):
        raise ValueError("user split leakage")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    reports = []
    metadata = pd.read_parquet(args.metadata) if args.family in ("fm", "both") else None
    fm_matrices = None
    if metadata is not None:
        fm_matrices = (_fm_matrices(_ordered(train), contexts, metadata),
                       _fm_matrices(_ordered(valid), contexts, metadata))
    for variant in ("public_only", "personal_only", "combined"):
        if args.family in ("gbt", "both"):
            report = fit_gbt(train, valid, variant, args.output_dir, args.trees, args.threads)
            reports.append(report)
            print(json.dumps({"family": report["family"], "variant": variant,
                              "validation": report["validation"]}), flush=True)
        if args.family in ("fm", "both"):
            report = fit_fm(train, valid, contexts, metadata, variant, args.output_dir,
                            args.epochs, args.batch_size, args.max_pairs_per_query, args.factors,
                            fm_matrices=fm_matrices)
            reports.append(report)
            print(json.dumps({"family": report["family"], "variant": variant,
                              "validation": report["validation"]}), flush=True)
    result = {"experiment": "evidence-aligned-v1r2-strong-weak-retrospective-observed-candidates-smoke",
              "dataset_manifest_sha256": digest(args.dataset / "manifest.json"),
              "trainer_sha256": digest(Path(__file__).resolve()),
              "sparse_feature_sha256": digest(Path(__file__).resolve().parent / "model_specific_features_v5.py"),
              "rating_semantics_sha256": digest(Path(__file__).resolve().parent / "rating_semantics_v7.py"),
              "metadata_sha256": digest(args.metadata) if metadata is not None else None,
              "same_validation_query_rows_for_both_families": True,
              "training_query_caveat": "GBT sees all training groups; FM pairs only queries with unequal shared gain.",
              "test_split_used": False, "hybrid": "NOT_BUILT: no OOF retrieval supplier",
              "seconds": time.monotonic() - started, "models": reports,
              "limitations": ["Current public/content metadata is retrospective, not as-of cutoff.",
                              "Observed future ratings are not full-catalogue exposure labels.",
                              "Pair sample counts are diagnostic; individual inclusion probabilities are not estimated.",
                              "This is a research MovieLens extraction with unverified original-release coverage."]}
    (args.output_dir / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
