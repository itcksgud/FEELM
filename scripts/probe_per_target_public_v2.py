"""Policy-free FEELM full-catalogue probe for public-evidence-v2 GBT/FM.

The output is an exposure diagnostic, NOT an offline satisfaction estimate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from build_evidence_rank_dataset_v1 import _evidence, _history_counters, _metadata_lookup
from build_service_user_k_comparison import HANGUL_RE, parse_markdown_ratings
from model_specific_features_v5 import FACTOR_FIELD_BUCKETS, build_sparse_content
from public_evidence_v2 import PublicCalibration, numeric_features_v2
from rating_semantics_v7 import utility
from train_per_target_rating_pilot_v1 import _tokens
from train_pointwise_rating_pilot_v1 import digest


PUBLIC_DATE = pd.Timestamp("2026-09-21")


def _content(catalog: pd.DataFrame):
    decorated = catalog.copy()
    joint = []
    for row in decorated.itertuples(index=False):
        record = row._asdict()
        countries = _tokens(record.get("origin_country_codes")) or _tokens(record.get("production_country_codes"))
        genres = _tokens(record.get("genre_ids"))
        joint.append([f"{country}|{genre}" for country in countries for genre in genres])
    decorated["joint_country_genre_tokens"] = joint
    return build_sparse_content(decorated, fields=FACTOR_FIELD_BUCKETS +
                                (("country_genre", "joint_country_genre_tokens", 2048),))


def _history_vote_context(stars: np.ndarray, votes: np.ndarray) -> dict[str, float]:
    weights = utility(stars)
    log_votes = np.log1p(np.maximum(np.nan_to_num(votes, nan=0), 0))
    pos, neg = np.maximum(weights, 0), np.maximum(-weights, 0)
    known = np.isfinite(votes) & (votes > 0)
    return {
        "positive_mean": float(np.dot(pos, log_votes) / pos.sum()) if pos.sum() > 0 else 0.0,
        "negative_mean": float(np.dot(neg, log_votes) / neg.sum()) if neg.sum() > 0 else 0.0,
        "positive_present": float(pos.sum() > 0),
        "negative_present": float(neg.sum() > 0),
        "positive_known_share": float(np.dot(pos, known) / pos.sum()) if pos.sum() > 0 else 0.0,
        "negative_known_share": float(np.dot(neg, known) / neg.sum()) if neg.sum() > 0 else 0.0,
    }


def probe(catalog_path: Path, ratings_path: Path, gbt_path: Path, fm_path: Path,
          output: Path, *, user_ids: tuple[int, ...] = (8, 11, 7),
          chunk_size: int = 5000) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if chunk_size <= 0:
        raise ValueError("chunk size must be positive")
    reports = {family: json.loads((path.parent / "metrics.json").read_text(encoding="utf-8"))
               for family, path in (("gbt", gbt_path), ("fm", fm_path))}
    for family, path in (("gbt", gbt_path), ("fm", fm_path)):
        report = reports[family]
        if (report["experiment"] != "strict-per-target-public-evidence-v2-exploratory"
                or report["family"] != family or report["test_split_used"] is not False
                or report["model_sha256"] != digest(path)
                or report["sources"]["catalog"] != digest(catalog_path)):
            raise ValueError(f"model and catalogue source pin mismatch: {family}")
    if reports["gbt"]["calibration"] != reports["fm"]["calibration"]:
        raise ValueError("GBT/FM public calibration mismatch")
    calibration = PublicCalibration.from_dict(reports["gbt"]["calibration"])
    all_ratings = parse_markdown_ratings(ratings_path)
    ratings = all_ratings[all_ratings.movie_title.fillna("").map(lambda title: bool(HANGUL_RE.search(str(title))))]
    catalog = pd.read_parquet(catalog_path).sort_values("movie_id", kind="stable").reset_index(drop=True)
    if (catalog.movie_id.duplicated().any() or catalog.tmdb_id.duplicated().any()
            or not catalog.movie_id.equals(catalog.service_movie_id)):
        raise ValueError("catalogue ID mismatch")
    ids = catalog.movie_id.to_numpy(np.int64)
    dates = pd.to_datetime(catalog.release_date, errors="coerce")
    release_ok = dates.notna() & dates.le(PUBLIC_DATE)
    metadata_cols = ["movie_id", "tmdb_id", "release_year", "collection_ids", "director_ids",
                     "top5_cast_ids", "keyword_ids", "origin_country_codes",
                     "production_country_codes", "genre_ids", "tmdb_vote_average", "tmdb_vote_count"]
    lookup = _metadata_lookup(catalog[metadata_cols],
                              catalog[["tmdb_id", "tmdb_vote_average", "tmdb_vote_count",
                                       "kobis_link_status", "kobis_audience_cumulative"]])
    content = _content(catalog)
    booster = xgb.Booster()
    booster.load_model(gbt_path)
    with np.load(fm_path) as state:
        linear = state["linear"].copy()
        factor = state["factor"].copy()
        bias = float(state["bias"][0])
        fm_names = list(state["feature_names"])
    if factor.shape[0] != content.shape[1]:
        raise ValueError("FM content width mismatch")
    selected = ratings[ratings.user_id.isin(user_ids)]
    if set(user_ids) != set(selected.user_id.astype(int)):
        raise ValueError("one or more users lack Korean-title ratings")
    top_records, risk_records = [], []
    for uid, user in selected.groupby("user_id", sort=True):
        user = user.sort_values(["created_at", "movie_id", "input_row"], kind="stable")
        history_ids = user.movie_id.to_numpy(np.int64)
        all_rated = all_ratings.loc[all_ratings.user_id.eq(uid), "movie_id"].to_numpy(np.int64)
        stars = user.score.to_numpy(np.float32)
        if not np.isfinite(stars).all() or not np.isin(history_ids, ids).all():
            raise ValueError("invalid FEELM history")
        candidates = np.flatnonzero(release_ok.to_numpy() & ~np.isin(ids, all_rated))
        counts = _history_counters(list(zip(history_ids.tolist(), stars.tolist(), strict=True)), lookup)
        weights = utility(stars)
        weights /= max(float(np.abs(weights).sum()), 1.0)
        history_positions = np.searchsorted(ids, history_ids)
        if not np.array_equal(ids[history_positions], history_ids):
            raise ValueError("history movie axis mismatch")
        profile = content[history_positions].multiply(weights[:, None]).sum(axis=0)
        profile_factors = np.asarray(profile @ factor).reshape(-1)
        history_votes = catalog.iloc[history_positions].tmdb_vote_count.to_numpy(np.float32)
        history_vote_context = _history_vote_context(stars, history_votes)
        scores = {"gbt": np.empty(len(candidates), np.float32),
                  "fm": np.empty(len(candidates), np.float32)}
        relation = np.empty(len(candidates), object)
        series_support = np.zeros(len(candidates), np.int32)
        director_support = np.zeros(len(candidates), np.int32)
        cast_support = np.zeros(len(candidates), np.int32)
        style = np.asarray([stars.mean(), stars.std(), (stars >= 3).mean(),
                            (stars <= 2.5).mean()], np.float32)
        for start in range(0, len(candidates), chunk_size):
            stop = min(start + chunk_size, len(candidates))
            positions = candidates[start:stop]
            frame = pd.DataFrame([_evidence(lookup[int(ids[pos])], counts) for pos in positions])
            frame["k"] = len(user)
            for name, value in zip(("history_rating_mean", "history_rating_std",
                                    "history_positive_share", "history_negative_share"), style, strict=True):
                frame[name] = value
            context = {name: np.full(len(frame), value, np.float32)
                       for name, value in history_vote_context.items()}
            matrix, names = numeric_features_v2(frame, calibration, vote_context=context)
            if names != reports["gbt"]["model"]["features"] or names != fm_names:
                raise ValueError("training/inference feature order mismatch")
            scores["gbt"][start:stop] = booster.predict(xgb.DMatrix(matrix))
            cf = np.asarray(content[positions] @ factor)
            scores["fm"][start:stop] = bias + matrix @ linear + cf @ profile_factors
            relation[start:stop] = frame.relation_evidence_state.to_numpy()
            for target, axis in ((series_support, "collection"),
                                 (director_support, "director"), (cast_support, "cast")):
                target[start:stop] = frame[f"{axis}_strong_pos_count"].fillna(0).to_numpy(np.int32)
        if not all(np.isfinite(score).all() for score in scores.values()):
            raise ValueError("nonfinite full-catalogue score")
        candidate_frame = catalog.iloc[candidates]
        vote = pd.to_numeric(candidate_frame.tmdb_vote_count, errors="coerce").fillna(0).to_numpy()
        kobis = (candidate_frame.kobis_link_status.fillna("").astype(str).str.startswith("VERIFIED") &
                 candidate_frame.kobis_audience_cumulative.notna()).to_numpy()
        for family, score in scores.items():
            order = np.lexsort((ids[candidates], -score))[:50]
            first = order[:10]
            risk_records.append({
                "user_id": int(uid), "nickname": str(user.nickname.iloc[0]), "k": len(user),
                "model": family, "candidate_count": len(candidates),
                "top10_tmdb_under100_no_kobis": int(((vote[first] < 100) & ~kobis[first]).sum()),
                "top10_tmdb_atmost3_no_kobis": int(((vote[first] <= 3) & ~kobis[first]).sum()),
                "top10_kobis": int(kobis[first].sum()),
                "top10_relation_none": int((relation[first] == "NONE").sum()),
                "top10_cast_only_strong": int(((cast_support[first] > 0) &
                                                (series_support[first] == 0) &
                                                (director_support[first] == 0)).sum()),
            })
            for rank, relative in enumerate(order, 1):
                film = candidate_frame.iloc[relative]
                top_records.append({
                    "user_id": int(uid), "nickname": str(user.nickname.iloc[0]), "k": len(user),
                    "model": family, "rank": rank, "movie_id": int(film.movie_id),
                    "title": film.title, "score": float(score[relative]),
                    "release_year": film.release_year,
                    "tmdb_vote_average": film.tmdb_vote_average,
                    "tmdb_vote_count": film.tmdb_vote_count,
                    "kobis_audience_cumulative": film.kobis_audience_cumulative,
                    "relation_evidence_state": relation[relative],
                    "series_strong_support": int(series_support[relative]),
                    "director_strong_support": int(director_support[relative]),
                    "cast_strong_support": int(cast_support[relative]),
                })
        print(json.dumps({"user_id": int(uid), "k": len(user),
                          "candidate_count": len(candidates)}, ensure_ascii=False), flush=True)
    output.mkdir(parents=True, exist_ok=False)
    pd.DataFrame(top_records).to_csv(output / "top50.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(risk_records).to_csv(output / "top10_risk.csv", index=False, encoding="utf-8-sig")
    report = {
        "experiment": "strict-per-target-public-v2-feelm-unlabeled-probe",
        "code_sha256": digest(Path(__file__).resolve()),
        "sources": {"catalog": digest(catalog_path), "ratings": digest(ratings_path),
                    "gbt": digest(gbt_path), "fm": digest(fm_path)},
        "user_ids": list(user_ids), "english_only_input_excluded": int(len(all_ratings) - len(ratings)),
        "exclusion": "every rated movie, including English-only title ratings",
        "release_rule": "known release_date <= 2026-09-21", "public_gate": False,
        "risk": risk_records,
        "outputs": {name: digest(output / name) for name in ("top50.csv", "top10_risk.csv")},
        "limitations": ["No relevance labels for unviewed films", "Selected users not representative",
                        "Current metadata attached retrospectively to ML32 training"]}
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("catalog", "ratings", "gbt", "fm", "output_dir"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    parser.add_argument("--user-ids", type=int, nargs="+", default=[8, 11, 7])
    parser.add_argument("--chunk-size", type=int, default=5000)
    args = parser.parse_args()
    result = probe(args.catalog, args.ratings, args.gbt, args.fm, args.output_dir,
                   user_ids=tuple(args.user_ids), chunk_size=args.chunk_size)
    print(json.dumps(result["risk"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
