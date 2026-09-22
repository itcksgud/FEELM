"""Static v11r3c eligibility on frozen user-8 v9 Top100; not new recommendations."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

import numpy as np
import pandas as pd

from build_service_user_k_comparison import HANGUL_RE, parse_markdown_ratings
from public_evidence_v2 import PublicCalibration
from train_pointwise_rating_pilot_v1 import digest


DATE = pd.Timestamp("2026-09-21")
CASES = {687163: "project_hail_mary", 1007757: "swapped_friends", 980431: "aang",
         936075: "michael"}
V9_GBT_SHA = "a9410ce37f0e7a37eab7cf592e8c4c1b00bd00fe2502cb755b353c04e48f5b63"


def ids(value) -> frozenset:
    if value is None or value is pd.NA or (isinstance(value, float) and np.isnan(value)):
        return frozenset()
    if isinstance(value, (list, tuple, np.ndarray)):
        return frozenset(x for x in value if x is not None)
    return frozenset([value])


def direct_evidence(candidate: pd.Series, history: pd.DataFrame) -> dict:
    collections = ids(candidate.collection_ids)
    directors = ids(candidate.director_ids)
    cast = ids(candidate.top5_cast_ids)
    def one_side(part: pd.DataFrame) -> dict:
        series_movies = set()
        series_sources = set()
        contributions = []
        other = defaultdict(list)
        for row in part.itertuples(index=False):
            his_collections = ids(row.collection_ids)
            source = ("collection", next(iter(his_collections))) if len(his_collections) == 1 else ("movie", int(row.movie_id))
            if collections & his_collections:
                series_movies.add(int(row.movie_id))
                series_sources.add(source)
                contributions.append({"relation": "collection", "source_key": [source[0], int(source[1])],
                                      "movies": [{"movie_id": int(row.movie_id), "score": float(row.score)}],
                                      "shared_ids": sorted(int(x) for x in collections & his_collections)})
            else:
                other[source].append(row)
        director_sources = set()
        cast_sources = {}
        for source, movies in other.items():
            if source in series_sources:
                continue
            if any(directors & ids(movie.director_ids) for movie in movies):
                director_sources.add(source)
                matches = [movie for movie in movies if directors & ids(movie.director_ids)]
                contributions.append({"relation": "director", "source_key": [source[0], int(source[1])],
                                      "movies": [{"movie_id": int(movie.movie_id), "score": float(movie.score)} for movie in matches],
                                      "shared_ids": sorted(int(x) for x in set().union(*(directors & ids(movie.director_ids) for movie in matches)))})
            else:
                people = set().union(*(cast & ids(movie.top5_cast_ids) for movie in movies))
                if people:
                    cast_sources[source] = people
                    matches = [movie for movie in movies if cast & ids(movie.top5_cast_ids)]
                    contributions.append({"relation": "cast", "source_key": [source[0], int(source[1])],
                                          "movies": [{"movie_id": int(movie.movie_id), "score": float(movie.score)} for movie in matches],
                                          "shared_ids": sorted(int(x) for x in people)})
        shared_people = set().union(*cast_sources.values()) if cast_sources else set()
        actor_units = min(2, len(cast_sources), len(shared_people))
        return {"series": len(series_movies), "director": len(director_sources),
                "cast_sources": len(cast_sources), "cast_people": len(shared_people),
                "cast_units": actor_units,
                "units": len(series_movies) + len(director_sources) + actor_units,
                "contributions": contributions,
                "high_specificity": bool(series_movies or director_sources or
                                         (len(cast_sources) >= 2 and len(shared_people) >= 2))}
    return {"positive": one_side(history.loc[history.score.ge(4)]),
            "negative": one_side(history.loc[history.score.le(2.5)])}


def requirement(d_tmdb: float, d_kobis: float | None, lcb: float | None,
                lcb_ref: float | None, votes: float, reference_votes: float) -> tuple[int, int, int]:
    if not np.isfinite(d_tmdb) or not np.isfinite(votes) or not np.isfinite(reference_votes):
        raise ValueError("invalid TMDB reference gap")
    d = min(d_tmdb, d_kobis) if d_kobis is not None else d_tmdb
    volume = int(np.floor(max(0.0, d) + .5))
    d_abs = max(0.0, np.log10((reference_votes + 1) / (votes + 1)))
    absolute = max(0, int(np.ceil(d_abs - .1)))
    if absolute > 0 and d_kobis is not None:
        absolute = max(1, min(absolute, int(np.floor(d_kobis + .5))))
    quality = int(lcb is None or lcb_ref is None or lcb < lcb_ref)
    return volume, absolute, quality


def run(catalog_path: Path, ratings_path: Path, top_path: Path, model_metrics: Path,
        output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    source_report = json.loads((top_path.parent / "summary.json").read_text(encoding="utf-8"))
    if (source_report["source_sha256"][str(catalog_path)] != digest(catalog_path)
            or source_report["source_sha256"][str(ratings_path)] != digest(ratings_path)
            or source_report["top100_sha256"] != digest(top_path)):
        raise ValueError("frozen Top100 source mismatch")
    metrics = json.loads(model_metrics.read_text(encoding="utf-8"))
    expected_catalog = digest(catalog_path)
    expected_model = source_report["source_sha256"].get(str(model_metrics.parent / "gbt-per-target.json"))
    if (metrics.get("family") != "gbt" or metrics.get("sources", {}).get("catalog") != expected_catalog
            or expected_model != V9_GBT_SHA or metrics.get("model_sha256") != expected_model
            or digest(model_metrics.parent / "gbt-per-target.json") != expected_model):
        raise ValueError("calibration source/model mismatch")
    calibration = PublicCalibration.from_dict(metrics["calibration"])
    catalog = pd.read_parquet(catalog_path).sort_values("movie_id", kind="stable").reset_index(drop=True)
    if catalog.movie_id.duplicated().any() or catalog.tmdb_id.duplicated().any():
        raise ValueError("catalog identity is not unique")
    catalog["release_parsed"] = pd.to_datetime(catalog.release_date, errors="coerce")
    catalog = catalog.loc[catalog.release_parsed.le(DATE) & catalog.status.eq("Released") & ~catalog.adult].copy()
    votes = pd.to_numeric(catalog.tmdb_vote_count, errors="coerce").to_numpy(np.float64)
    average = pd.to_numeric(catalog.tmdb_vote_average, errors="coerce").to_numpy(np.float64)
    valid = np.isfinite(votes) & (votes > 0) & (votes == np.floor(votes)) & np.isfinite(average) & (average > 0) & (average <= 10)
    safe_votes = np.where(valid, votes, 0)
    posterior = (safe_votes * np.where(valid, average, calibration.prior_mean) +
                 calibration.prior_votes * calibration.prior_mean) / (safe_votes + calibration.prior_votes)
    uncertainty = np.sqrt(calibration.between_variance * calibration.rating_noise_variance /
                          (calibration.rating_noise_variance + safe_votes * calibration.between_variance))
    catalog["lcb"] = np.where(valid, posterior - uncertainty, np.nan)
    catalog["votes_safe"] = np.where(valid, votes, 0)
    catalog["audience_safe"] = pd.to_numeric(catalog.kobis_audience_cumulative, errors="coerce").where(
        catalog.kobis_value_valid.fillna(False) & catalog.kobis_audience_cumulative.gt(0))
    country = defaultdict(set)
    genre = defaultdict(set)
    animation = {True: set(), False: set()}
    for index, row in enumerate(catalog.itertuples(index=False)):
        countries = ids(row.origin_country_codes) or frozenset(["UNKNOWN"])
        genres = ids(row.genre_ids) or frozenset([-1])
        for item in countries:
            country[item].add(index)
        for item in genres:
            genre[item].add(index)
        animation[16 in genres].add(index)
    universe = set(range(len(catalog)))
    cache = {}
    def indices(c: str | None, a: bool | None, g: int | None) -> np.ndarray:
        key = (c, a, g)
        if key not in cache:
            selected = set(universe)
            if c is not None:
                selected.intersection_update(country.get(c, set()))
            if a is not None:
                selected.intersection_update(animation[a])
            if g is not None:
                selected.intersection_update(genre.get(g, set()))
            cache[key] = np.asarray(sorted(selected), np.int64)
        return cache[key]
    def reference(item: pd.Series, channel: str, c: str, g: int):
        a = 16 in ids(item.genre_ids)
        for key in [(c, a, g), (c, a, None), (c, None, None), (None, None, None)]:
            subset = catalog.iloc[indices(*key)]
            subset = subset.loc[subset.movie_id.ne(item.movie_id)]
            if channel == "tmdb":
                subset = subset.loc[subset.tmdb_public_score.notna() & subset.votes_safe.gt(0)]
            else:
                subset = subset.loc[subset.audience_safe.notna()]
            if len(subset) < 30:
                continue
            if channel == "tmdb":
                top = subset.sort_values(["tmdb_public_score", "tmdb_id"],
                                         ascending=[False, True], kind="stable").head(10)
                return float(top.votes_safe.median()), float(top.lcb.median()), key
            return float(subset.audience_safe.quantile(.9)), None, key
        return None, None, None
    ratings = parse_markdown_ratings(ratings_path)
    user = ratings.loc[ratings.user_id.eq(8)].copy()
    user = user.loc[user.movie_title.fillna("").map(lambda title: bool(HANGUL_RE.search(str(title))))]
    user = user.sort_values(["created_at", "movie_id", "input_row"], kind="stable")
    if len(user) != 52:
        raise ValueError("user8 Korean-title actual history changed")
    metadata = catalog[["movie_id", "collection_ids", "director_ids", "top5_cast_ids"]]
    top = pd.read_csv(top_path, encoding="utf-8-sig")
    top = top.loc[top.model.isin(["v9_gbt", "v9_fm"]) & top.k.isin([10, 52])].copy()
    records = []
    for k in (10, 52):
        history = user.iloc[:k][["movie_id", "score"]].merge(metadata, on="movie_id", how="left", validate="one_to_one")
        if history.collection_ids.isna().any():
            raise ValueError("input film metadata missing")
        for row in top.loc[top.k.eq(k)].itertuples(index=False):
            item = catalog.loc[catalog.movie_id.eq(row.movie_id)]
            if len(item) != 1:
                raise ValueError(f"candidate {row.movie_id} missing from verified catalog")
            item = item.iloc[0]
            countries = ids(item.origin_country_codes) or frozenset(["UNKNOWN"])
            genres = ids(item.genre_ids) or frozenset([-1])
            tmdb_refs, kobis_refs, quality_refs = [], [], []
            for c in countries:
                for g in genres:
                    t_ref, q_ref, _ = reference(item, "tmdb", c, g)
                    if t_ref is not None:
                        tmdb_refs.append(t_ref)
                        quality_refs.append(q_ref)
                    if np.isfinite(item.audience_safe):
                        k_ref, _, _ = reference(item, "kobis", c, g)
                        if k_ref is not None:
                            kobis_refs.append(k_ref)
            if not tmdb_refs:
                records.append({"k": k, "model": row.model, "rank": int(row.rank),
                                "tmdb_id": int(row.tmdb_id), "status": "REFERENCE_UNAVAILABLE"})
                continue
            t_ref = max(tmdb_refs)
            d_t = max(0.0, np.log10((t_ref + 1) / (float(item.votes_safe) + 1)))
            d_k = (max(0.0, np.log10((max(kobis_refs) + 1) / (float(item.audience_safe) + 1)))
                   if kobis_refs else None)
            evidence = direct_evidence(item, history)
            volume, absolute, quality = requirement(d_t, d_k, float(item.lcb) if np.isfinite(item.lcb) else None,
                                                    max(quality_refs) if quality_refs else None,
                                                    float(item.votes_safe), calibration.reference_vote_quantile)
            required = max(volume, absolute, quality)
            passed = required == 0 or (evidence["positive"]["units"] >= required +
                                       2 * evidence["negative"]["units"] and
                                       evidence["positive"]["high_specificity"])
            records.append({"k": k, "model": row.model, "rank": int(row.rank),
                            "tmdb_id": int(row.tmdb_id), "case": CASES.get(int(row.tmdb_id), ""),
                            "score": float(row.score), "votes": int(item.votes_safe),
                            "kobis_audience": (None if not np.isfinite(item.audience_safe)
                                               else int(item.audience_safe)),
                            "tmdb_ref_max": t_ref, "kobis_ref_max": max(kobis_refs) if kobis_refs else None,
                            "d_tmdb": d_t, "d_kobis": d_k, "lcb": (None if not np.isfinite(item.lcb) else float(item.lcb)),
                            "lcb_ref_max": max(quality_refs) if quality_refs else None,
                            "required_volume": volume, "required_absolute": absolute,
                            "required_quality": quality,
                            "required": required, "positive": evidence["positive"],
                            "negative": evidence["negative"],
                            "status": "PASS" if passed else "FAIL"})
    result = {"variant": "v11r3-static-user8-top100-only",
              "sources_sha256": {"catalog": digest(catalog_path), "ratings": digest(ratings_path),
                                 "top100": digest(top_path), "model_metrics": digest(model_metrics)},
              "limitations": "Only frozen v9 Top100, not full-catalog rerank or satisfaction evaluation.",
              "records": records}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("catalog", "ratings", "top100", "model_metrics", "output"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.catalog, args.ratings, args.top100, args.model_metrics, args.output)
    print(json.dumps({"variant": result["variant"], "records": len(result["records"])}))
