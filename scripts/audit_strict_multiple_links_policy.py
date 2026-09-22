"""Replay frozen r3c diagnostics with the user-requested multiple-link policy."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

import pandas as pd

from train_pointwise_rating_pilot_v1 import digest
from audit_log_direct_user8_top100 import direct_evidence, ids
from build_service_user_k_comparison import HANGUL_RE, parse_markdown_ratings


def contextual_evidence(candidate: pd.Series, history: pd.DataFrame) -> dict:
    """A shared actor supports the exception only in the same country/genre/form."""
    qualified = history.copy(deep=True)
    candidate_country = ids(candidate.origin_country_codes)
    candidate_genres = ids(candidate.genre_ids)
    rejected = []
    for index, row in qualified.iterrows():
        history_genres = ids(row.genre_ids)
        context_match = (bool(candidate_country & ids(row.origin_country_codes))
                         and bool(candidate_genres & history_genres)
                         and ((16 in candidate_genres) == (16 in history_genres)))
        if not context_match:
            if ids(candidate.top5_cast_ids) & ids(row.top5_cast_ids):
                rejected.append(int(row.movie_id))
            qualified.at[index, "top5_cast_ids"] = []
    result = direct_evidence(candidate, qualified)
    result["cast_context_rejected_movie_ids"] = rejected
    return result


def required_links(votes: float, reference: float, d_tmdb: float,
                   d_kobis: float | None) -> tuple[float, int]:
    if not all(math.isfinite(x) and x >= 0 for x in (votes, reference, d_tmdb)) or reference == 0:
        raise ValueError("invalid public reference")
    if d_kobis is not None and (not math.isfinite(d_kobis) or d_kobis < 0):
        raise ValueError("invalid KOBIS gap")
    gap = 0. if votes >= reference else max(0., d_tmdb, math.log10(reference / max(1., votes)))
    if d_kobis is not None:
        gap = min(gap, d_kobis)
    return gap, 0 if gap == 0 else 2 + math.floor(gap)


def run(source: Path, metrics_path: Path, catalog_path: Path, ratings_path: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    previous = json.loads(source.read_text(encoding="utf-8"))
    if previous["sources_sha256"]["model_metrics"] != digest(metrics_path):
        raise ValueError("frozen calibration mismatch")
    for key, path in (("catalog", catalog_path), ("ratings", ratings_path)):
        if previous["sources_sha256"][key] != digest(path):
            raise ValueError(f"frozen {key} mismatch")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    reference = metrics["calibration"]["reference_vote_quantile"]
    if len(previous["records"]) != 400:
        raise ValueError("expected frozen four groups of Top100")
    catalog = pd.read_parquet(catalog_path).set_index("movie_id", drop=False)
    if not catalog.index.is_unique:
        raise ValueError("duplicate service movie identity")
    ratings = parse_markdown_ratings(ratings_path)
    user = ratings.loc[ratings.user_id.eq(8)].copy()
    user = user.loc[user.movie_title.fillna("").map(lambda title: bool(HANGUL_RE.search(str(title))))]
    user = user.sort_values(["created_at", "movie_id", "input_row"], kind="stable")
    if len(user) != 52:
        raise ValueError("frozen user8 input changed")
    metadata = catalog[["movie_id", "collection_ids", "director_ids", "top5_cast_ids",
                        "origin_country_codes", "genre_ids"]].reset_index(drop=True)
    histories = {k: user.iloc[:k][["movie_id", "score"]].merge(metadata, on="movie_id", how="left",
                                                             validate="one_to_one") for k in (10, 52)}
    if any(history.collection_ids.isna().any() for history in histories.values()):
        raise ValueError("input metadata missing")
    by_tmdb = catalog.reset_index(drop=True).set_index("tmdb_id")
    if not by_tmdb.index.is_unique:
        raise ValueError("duplicate TMDB identity")
    records = []
    groups = defaultdict(Counter)
    for old in previous["records"]:
        row = dict(old)
        row["previous_status"] = old["status"]
        if old["status"] == "REFERENCE_UNAVAILABLE":
            row["status"] = "REFERENCE_UNAVAILABLE"
        else:
            gap, required = required_links(old["votes"], reference, old["d_tmdb"], old["d_kobis"])
            evidence = contextual_evidence(by_tmdb.loc[old["tmdb_id"]], histories[old["k"]])
            pos, neg = evidence["positive"], evidence["negative"]
            passed = required == 0 or (pos["units"] >= required + 2 * neg["units"] and pos["high_specificity"])
            row.update({"combined_gap": gap, "strict_required": required,
                        "previous_positive": old["positive"], "previous_negative": old["negative"],
                        "positive": pos, "negative": neg,
                        "cast_context_rejected_movie_ids": evidence["cast_context_rejected_movie_ids"],
                        "status": "PASS" if passed else "FAIL"})
        records.append(row)
        groups[(row["k"], row["model"])].update([row["status"]])
    result = {"variant": "strict-multiple-links-context-static-r3e", "reference_votes": reference,
              "source_sha256": digest(source), "metrics_sha256": digest(metrics_path),
              "script_sha256": digest(Path(__file__)),
              "inherited_sources_sha256": previous["sources_sha256"],
              "limitations": "Frozen old Top100 only; no full-catalog replacement or satisfaction evaluation.",
              "groups": [{"k": k, "model": model, **counts} for (k, model), counts in sorted(groups.items())],
              "records": records}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("source", "metrics", "catalog", "ratings", "output"):
        parser.add_argument(f"--{arg}", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.source, args.metrics, args.catalog, args.ratings, args.output)
    print(json.dumps(result["groups"]))
