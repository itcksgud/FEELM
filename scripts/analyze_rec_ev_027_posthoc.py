#!/usr/bin/env python3
"""Read-only failure analysis for the sealed REC-EV-027 development evidence.

The tables produced here are descriptive diagnostics. They do not reopen model
selection, prove Korean-user/new-release performance, or update product policy.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable
import zipfile

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "outputs/recommendation-evidence/rec-ev-027"
DEFAULT_OUTPUT = ROOT / "outputs/recommendation-evidence/rec-ev-027-posthoc"
CONTRACT = ROOT / "docs/recommendation/contracts/rec-ev-027-strict-item-cold-model-screen.json"
OUTERS = ("R1", "R2", "R3", "R4", "KR", "RECENT")
MODELS = ("STRUCTURED_DIRECT", "FEATURE_ONLY_LIGHTFM")
ENCODINGS = ("PERCENTILE_MAGNITUDE", "BINARY_SIGN")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_snapshot() -> dict[str, tuple[int, int]]:
    return {
        path.relative_to(SOURCE).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(SOURCE.rglob("*"))
        if path.is_file()
    }


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8")
    os.replace(temporary, path)


def history_bucket(value: int) -> str:
    if value < 50:
        return "20_49"
    if value < 100:
        return "50_99"
    if value < 500:
        return "100_499"
    return "500_PLUS"


def popularity_bucket(value: int) -> str:
    if value < 10:
        return "1_9"
    if value < 50:
        return "10_49"
    if value < 200:
        return "50_199"
    if value < 1000:
        return "200_999"
    return "1000_PLUS"


def release_bucket(year: float) -> str:
    if not np.isfinite(year):
        return "UNKNOWN"
    value = int(year)
    if value < 1980:
        return "PRE_1980"
    if value < 2000:
        return "1980_1999"
    if value < 2010:
        return "2000_2009"
    if value < 2020:
        return "2010_2019"
    if value <= 2023:
        return "2020_2023"
    return "2024_PLUS"


def equal_count_quartile(frame: pd.DataFrame, group: list[str], value: str) -> pd.Series:
    order = group + [value, "user_key"]
    ordered = frame.sort_values(order, kind="stable").copy()
    ordered["_ordinal"] = ordered.groupby(group, sort=False).cumcount()
    ordered["_size"] = ordered.groupby(group, sort=False)["user_key"].transform("size")
    ordered["_quartile"] = np.minimum(4 * ordered["_ordinal"] // ordered["_size"] + 1, 4)
    return ordered.set_index(group + ["user_key"])["_quartile"].astype(int)


def outer_files(slug: str) -> dict[str, Path]:
    base = SOURCE / "replication" / slug
    return {
        "prior": base / "prepared/warm-prior.npz",
        "profiles": base / "prepared/evaluation-profiles.parquet",
        "labels": base / "evaluation-labels.parquet",
        "scores": base / "score-ranks.parquet",
        "metrics": base / "user-metrics.parquet",
    }


def load_profiles() -> pd.DataFrame:
    from rec_ev_027_core import profile_weights

    rows: list[dict[str, Any]] = []
    for slug in OUTERS:
        paths = outer_files(slug)
        profiles = pd.read_parquet(paths["profiles"]).sort_values("user_key", kind="stable")
        prior = np.load(paths["prior"], allow_pickle=False)["g0_mid"]
        for profile in profiles.itertuples(index=False):
            indices = np.asarray(profile.profile_rating_idx, dtype=np.int8)
            percentile = profile_weights(indices, prior, "PERCENTILE_MAGNITUDE")
            rows.append(
                {
                    "outer": slug,
                    "user_key": str(profile.user_key),
                    "profile_unique_ratings": int(np.unique(indices).size),
                    "profile_positive": int(np.count_nonzero(percentile > 0)),
                    "profile_negative": int(np.count_nonzero(percentile < 0)),
                    "profile_zero": int(np.count_nonzero(percentile == 0)),
                    "profile_abs_signal": float(np.abs(percentile).sum()),
                    "profile_weight_std": float(percentile.std(ddof=0)),
                }
            )
    result = pd.DataFrame(rows)
    quartiles = equal_count_quartile(result, ["outer"], "profile_abs_signal")
    result["profile_signal_quartile"] = [
        f"Q{int(quartiles.loc[(row.outer, row.user_key)])}"
        for row in result.itertuples(index=False)
    ]
    return result


def archive_path_and_member(contract: dict[str, Any]) -> tuple[Path, str]:
    spec = contract["allowed_input_artifacts"]["movielens_archive"]
    path = Path(spec["path"])
    if not path.is_absolute():
        path = ROOT / path
    if path.stat().st_size != int(spec["bytes"]) or sha256_file(path) != spec["sha256"]:
        raise RuntimeError("MovieLens archive pin drift")
    return path, str(spec["member"])


def raw_counts(contract: dict[str, Any], users: Iterable[str], movies: Iterable[int]) -> tuple[dict[str, int], dict[int, int]]:
    from rec_ev_022a_core import user_key

    wanted_users = set(map(str, users))
    wanted_movies = set(map(int, movies))
    uid_to_key: dict[int, str] = {}
    for uid in range(1, 300_001):
        key = user_key(uid)
        if key in wanted_users:
            uid_to_key[uid] = key
    if len(uid_to_key) != len(wanted_users):
        raise RuntimeError("evaluation user reverse lookup drift")
    histories: Counter[str] = Counter()
    movie_counts: Counter[int] = Counter()
    archive, member = archive_path_and_member(contract)
    with zipfile.ZipFile(archive) as zf, zf.open(member) as handle:
        if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
            raise RuntimeError("MovieLens header drift")
        for raw in handle:
            first = raw.find(b",")
            second = raw.find(b",", first + 1)
            if first <= 0 or second <= first + 1:
                raise RuntimeError("malformed MovieLens row")
            uid = int(raw[:first])
            movie = int(raw[first + 1 : second])
            key = uid_to_key.get(uid)
            if key is not None:
                histories[key] += 1
            if movie in wanted_movies:
                movie_counts[movie] += 1
    if set(histories) != wanted_users or set(movie_counts) != wanted_movies:
        raise RuntimeError("raw diagnostic count coverage drift")
    return dict(histories), dict(movie_counts)


def paired_user_metrics(profiles: pd.DataFrame, history_counts: dict[str, int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for slug in OUTERS:
        metrics = pd.read_parquet(outer_files(slug)["metrics"])
        metrics = metrics.loc[metrics["model"].isin(MODELS) & metrics["active"]].copy()
        direct = metrics.loc[metrics["model"].eq("STRUCTURED_DIRECT")].drop(columns="model")
        learned = metrics.loc[metrics["model"].eq("FEATURE_ONLY_LIGHTFM")].drop(columns="model")
        paired = learned.merge(
            direct,
            on=["user_key", "track", "fold_or_domain", "encoding"],
            suffixes=("_lightfm", "_direct"),
            validate="one_to_one",
        )
        paired["outer"] = slug
        paired["harm_benefit"] = paired["HARM20_direct"] - paired["HARM20_lightfm"]
        paired["mean_q_benefit"] = paired["TOP2_MEAN_Q_lightfm"] - paired["TOP2_MEAN_Q_direct"]
        paired["min_q_benefit"] = paired["TOP2_MIN_Q_lightfm"] - paired["TOP2_MIN_Q_direct"]
        frames.append(paired)
    result = pd.concat(frames, ignore_index=True)
    result = result.merge(profiles, on=["outer", "user_key"], how="left", validate="many_to_one")
    result["history_n"] = result["user_key"].map(history_counts)
    if result["history_n"].isna().any() or result["profile_abs_signal"].isna().any():
        raise RuntimeError("missing user diagnostic attributes")
    result["history_n"] = result["history_n"].astype(int)
    result["history_bucket"] = result["history_n"].map(history_bucket)
    result["lightfm_harm"] = result["HARM20_lightfm"]
    result["direct_harm"] = result["HARM20_direct"]
    result["lightfm_beats_direct_harm"] = result["harm_benefit"] > 0
    result["lightfm_beats_direct_mean_q"] = result["mean_q_benefit"] > 0
    return result


def summarize_user_segments(paired: pd.DataFrame) -> pd.DataFrame:
    outputs: list[pd.DataFrame] = []
    for segment in ("history_bucket", "profile_signal_quartile", "profile_positive"):
        grouped = (
            paired.groupby(["outer", "encoding", segment], dropna=False, as_index=False)
            .agg(
                users=("user_key", "nunique"),
                lightfm_harm=("lightfm_harm", "mean"),
                direct_harm=("direct_harm", "mean"),
                harm_benefit=("harm_benefit", "mean"),
                mean_q_benefit=("mean_q_benefit", "mean"),
                min_q_benefit=("min_q_benefit", "mean"),
                lightfm_beats_direct_harm_share=("lightfm_beats_direct_harm", "mean"),
                lightfm_beats_direct_mean_q_share=("lightfm_beats_direct_mean_q", "mean"),
                mean_seed_top2_overlap=("seed_top2_overlap_lightfm", "mean"),
            )
            .rename(columns={segment: "segment_value"})
        )
        grouped.insert(2, "segment", segment)
        grouped["segment_value"] = grouped["segment_value"].astype(str)
        outputs.append(grouped)
    return pd.concat(outputs, ignore_index=True).sort_values(
        ["outer", "encoding", "segment", "segment_value"], kind="stable", ignore_index=True
    )


def catalog_metadata(contract: dict[str, Any], movie_counts: dict[int, int]) -> pd.DataFrame:
    outputs = contract["catalog_content_build"]["outputs"]
    structured = pd.read_parquet(ROOT / outputs["structured"], columns=["movie_id", "release_year", "missing_mask"])
    domain = pd.read_parquet(ROOT / outputs["domain_projection"], columns=["movie_id", "tmdb_korean_origin_proxy"])
    result = structured.merge(domain, on="movie_id", how="left", validate="one_to_one")
    result = result.loc[result["movie_id"].isin(movie_counts)].copy()
    result["catalog_rating_count"] = result["movie_id"].map(movie_counts).astype(int)
    result["popularity_bucket"] = result["catalog_rating_count"].map(popularity_bucket)
    result["release_bucket"] = result["release_year"].map(release_bucket)
    result["missing_feature_groups"] = result["missing_mask"].astype(int).map(int.bit_count)
    return result


def explode_candidate_rows(metadata: pd.DataFrame) -> pd.DataFrame:
    outputs: list[pd.DataFrame] = []
    for slug in OUTERS:
        paths = outer_files(slug)
        labels = pd.read_parquet(paths["labels"]).set_index("user_key")
        scores = pd.read_parquet(paths["scores"])
        scores = scores.loc[
            scores["encoding"].eq("PERCENTILE_MAGNITUDE")
            & scores["model"].isin(MODELS)
            & scores["active"]
        ]
        for row in scores.itertuples(index=False):
            label = labels.loc[str(row.user_key)]
            target_ids = np.asarray(row.target_movie_ids, dtype=np.int64)
            label_ids = np.asarray(label.target_movie_ids, dtype=np.int64)
            if not np.array_equal(target_ids, label_ids):
                raise RuntimeError("score-label target order drift")
            q = np.asarray(label.target_q_eval, dtype=np.float64)
            order = np.asarray(row.ranked_target_indices, dtype=np.int64)
            selected = np.zeros(len(target_ids), dtype=bool)
            selected[order[:2]] = True
            outputs.append(
                pd.DataFrame(
                    {
                        "outer": slug,
                        "user_key": str(row.user_key),
                        "model": str(row.model),
                        "movie_id": target_ids,
                        "q_eval": q,
                        "selected": selected,
                    }
                )
            )
    candidates = pd.concat(outputs, ignore_index=True)
    candidates = candidates.merge(metadata, on="movie_id", how="left", validate="many_to_one")
    if candidates["catalog_rating_count"].isna().any():
        raise RuntimeError("candidate metadata coverage drift")
    candidates["selected_q"] = candidates["q_eval"].where(candidates["selected"])
    candidates["selected_harm"] = (candidates["selected"] & candidates["q_eval"].le(0.20)).astype(int)
    candidates["selected_good"] = (candidates["selected"] & candidates["q_eval"].ge(0.80)).astype(int)
    return candidates


def summarize_movie_segments(candidates: pd.DataFrame) -> pd.DataFrame:
    outputs: list[pd.DataFrame] = []
    for segment in ("release_bucket", "popularity_bucket", "tmdb_korean_origin_proxy", "missing_feature_groups"):
        grouped = (
            candidates.groupby(["outer", "model", segment], dropna=False, as_index=False)
            .agg(
                candidate_rows=("movie_id", "size"),
                unique_movies=("movie_id", "nunique"),
                selected_rows=("selected", "sum"),
                selected_rate=("selected", "mean"),
                selected_q_mean=("selected_q", "mean"),
                selected_harm_rows=("selected_harm", "sum"),
                selected_good_rows=("selected_good", "sum"),
            )
            .rename(columns={segment: "segment_value"})
        )
        grouped.insert(2, "segment", segment)
        grouped["segment_value"] = grouped["segment_value"].astype(str)
        grouped["selected_harm_share"] = grouped["selected_harm_rows"] / grouped["selected_rows"]
        grouped["selected_good_share"] = grouped["selected_good_rows"] / grouped["selected_rows"]
        outputs.append(grouped)
    return pd.concat(outputs, ignore_index=True).sort_values(
        ["outer", "model", "segment", "segment_value"], kind="stable", ignore_index=True
    )


def concentration(candidates: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    selected = candidates.loc[candidates["selected"]]
    for (outer, model), group in selected.groupby(["outer", "model"], sort=True):
        counts = group.groupby("movie_id").size().to_numpy(dtype=float)
        total = float(counts.sum())
        shares = counts / total
        rows.append(
            {
                "outer": outer,
                "model": model,
                "top2_rows": int(total),
                "unique_selected_movies": int(len(counts)),
                "top10_movie_share": float(np.sort(counts)[-10:].sum() / total),
                "selection_hhi": float(np.square(shares).sum()),
            }
        )
    return pd.DataFrame(rows).sort_values(["outer", "model"], kind="stable", ignore_index=True)


def headline(paired: pd.DataFrame, movie_segments: pd.DataFrame) -> dict[str, Any]:
    primary = paired.loc[paired["encoding"].eq("PERCENTILE_MAGNITUDE")]
    random = primary.loc[primary["outer"].isin(("R1", "R2", "R3", "R4"))]
    by_outer = (
        primary.groupby("outer", as_index=False)
        .agg(
            users=("user_key", "nunique"),
            lightfm_harm=("lightfm_harm", "mean"),
            direct_harm=("direct_harm", "mean"),
            harm_benefit=("harm_benefit", "mean"),
            mean_q_benefit=("mean_q_benefit", "mean"),
            min_q_benefit=("min_q_benefit", "mean"),
            mean_seed_top2_overlap=("seed_top2_overlap_lightfm", "mean"),
        )
    )
    recent_pop = movie_segments.loc[
        movie_segments["outer"].eq("RECENT")
        & movie_segments["model"].eq("FEATURE_ONLY_LIGHTFM")
        & movie_segments["segment"].eq("popularity_bucket")
    ]
    return {
        "random_folds_unique_users": int(random["user_key"].nunique()),
        "random_folds_lightfm_harm": float(random["lightfm_harm"].mean()),
        "random_folds_direct_harm": float(random["direct_harm"].mean()),
        "random_folds_harm_benefit": float(random["harm_benefit"].mean()),
        "random_folds_mean_q_benefit": float(random["mean_q_benefit"].mean()),
        "random_folds_min_q_benefit": float(random["min_q_benefit"].mean()),
        "outer_summary": by_outer.to_dict("records"),
        "recent_lightfm_popularity_slices": recent_pop.to_dict("records"),
    }


def run(output_root: Path) -> dict[str, Any]:
    before = source_snapshot()
    contract = read_json(CONTRACT)
    replication = read_json(SOURCE / "replication/random-folds-analysis.json")
    transfer = read_json(SOURCE / "replication/proxy-transfer-analysis.json")
    if replication["status"] != "LEARNED_INCREMENT_REPLICATED":
        raise RuntimeError("unexpected replication truth")
    if transfer["track_truth"] != {"KR": "PROXY_SIGNAL", "RECENT": "PROXY_SIGNAL"}:
        raise RuntimeError("unexpected proxy truth")

    profiles = load_profiles()
    all_movies: set[int] = set()
    for slug in OUTERS:
        labels = pd.read_parquet(outer_files(slug)["labels"], columns=["target_movie_ids"])
        for values in labels["target_movie_ids"]:
            all_movies.update(map(int, values))
    histories, movie_counts = raw_counts(contract, profiles["user_key"], all_movies)
    paired = paired_user_metrics(profiles, histories)
    user_segments = summarize_user_segments(paired)
    metadata = catalog_metadata(contract, movie_counts)
    candidates = explode_candidate_rows(metadata)
    movie_segments = summarize_movie_segments(candidates)
    selected_concentration = concentration(candidates)

    if before != source_snapshot():
        raise RuntimeError("sealed REC-EV-027 source changed during post-hoc analysis")
    atomic_csv(output_root / "user-profiles.csv", profiles)
    atomic_csv(output_root / "paired-user-metrics.csv", paired)
    atomic_csv(output_root / "user-segment-summary.csv", user_segments)
    atomic_csv(output_root / "movie-segment-summary.csv", movie_segments)
    atomic_csv(output_root / "selection-concentration.csv", selected_concentration)
    summary = {
        "schema_version": 1,
        "evidence_id": "REC-EV-027-POSTHOC",
        "status": "POSTHOC_FAILURE_DIAGNOSTIC_ONLY",
        "claim_boundary": [
            "NOT_CONFIRMATORY",
            "NO_NEW_MODEL_OR_POLICY_SELECTION_FROM_OPENED_LABELS",
            "MOVIELENS_RATERS_ARE_NOT_KOREAN_USERS",
            "RETROSPECTIVE_2020_2023_IS_NOT_FUTURE_RELEASE_VALIDATION",
            "UNRATED_IS_UNKNOWN_NOT_NEGATIVE",
        ],
        "source_status": {
            "replication": replication["status"],
            "proxy_tracks": transfer["track_truth"],
            "locked_test_opened": False,
            "final_reserve_opened": False,
            "product_policy_changed": False,
        },
        "diagnostic_counts": {
            "outer_memberships": int(len(profiles)),
            "unique_users": int(profiles["user_key"].nunique()),
            "unique_target_movies": int(len(all_movies)),
            "candidate_model_rows": int(len(candidates)),
            "timestamps_parsed": 0,
        },
        "headline": headline(paired, movie_segments),
        "tables": {
            "user_profiles": "user-profiles.csv",
            "paired_user_metrics": "paired-user-metrics.csv",
            "user_segment_summary": "user-segment-summary.csv",
            "movie_segment_summary": "movie-segment-summary.csv",
            "selection_concentration": "selection-concentration.csv",
        },
        "sealed_source": {
            "path": SOURCE.relative_to(ROOT).as_posix(),
            "file_count": len(before),
            "unchanged_after_analysis": True,
        },
    }
    atomic_json(output_root / "posthoc-analysis.json", summary)
    if before != source_snapshot():
        raise RuntimeError("sealed REC-EV-027 source changed after post-hoc output write")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run(args.output_root.resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
