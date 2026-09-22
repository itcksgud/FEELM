from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


K_VALUES = (0, 1, 2, 3, 5, 10, 20, 40, 50)
POLICY_VERSION = "comparison-rerank-v1"
HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_markdown_ratings(path: Path) -> pd.DataFrame:
    rows = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    table = [line for line in rows if line.startswith("|") and line.endswith("|")]
    require(len(table) >= 3, "ratings export must contain a Markdown pipe table")
    header = [value.strip() for value in table[0].strip("|").split("|")]
    expected = {
        "user_id", "nickname", "user_rating_count", "movie_id", "movie_title",
        "score", "created_at",
    }
    require(expected.issubset(header), f"missing ratings columns: {sorted(expected - set(header))}")
    records: list[dict[str, str]] = []
    for line in table[2:]:
        values = [value.strip() for value in line.strip("|").split("|")]
        if len(values) == len(header):
            records.append(dict(zip(header, values, strict=True)))
    frame = pd.DataFrame(records)
    for column in ("user_id", "user_rating_count", "movie_id"):
        normalized = frame[column].astype(str).str.replace(",", "", regex=False)
        frame[column] = pd.to_numeric(normalized, errors="raise").astype("int64")
    normalized_score = frame["score"].astype(str).str.replace(",", "", regex=False)
    frame["score"] = pd.to_numeric(normalized_score, errors="raise").astype("float64")
    frame["created_at"] = pd.to_datetime(frame["created_at"], errors="raise", utc=True)
    frame["input_row"] = np.arange(len(frame), dtype=np.int64)
    require(frame["score"].between(0.5, 5.0).all(), "ratings must be in [0.5, 5.0]")
    require(not frame.duplicated(["user_id", "movie_id"]).any(), "duplicate user/movie ratings")
    return frame.sort_values(
        ["user_id", "created_at", "movie_id", "input_row"], kind="mergesort"
    ).reset_index(drop=True)


def _genre_tuple(value: object) -> tuple[int, ...]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ()
    if isinstance(value, np.ndarray):
        return tuple(int(item) for item in value.tolist())
    if isinstance(value, (list, tuple)):
        return tuple(int(item) for item in value)
    raise TypeError(f"unsupported genre_ids value: {type(value)!r}")


def _integer_vote_count(value: object) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return np.isfinite(number) and number >= 21 and number.is_integer()


def build_catalog(
    service_catalog_path: Path,
    metadata_path: Path,
    eligibility_catalog_path: Path,
    kobis_path: Path,
) -> pd.DataFrame:
    service = pd.read_csv(service_catalog_path, low_memory=False)
    require(service["service_movie_id"].is_unique, "service movie IDs must be unique")
    require(service["tmdb_id"].is_unique, "service TMDB IDs must be unique")
    metadata = pd.read_parquet(
        metadata_path,
        columns=[
            "movie_id", "tmdb_id", "genre_ids", "tmdb_vote_average", "tmdb_vote_count",
        ],
    )
    require(metadata["movie_id"].is_unique, "MovieLens metadata movie IDs must be unique")
    require(metadata["tmdb_id"].is_unique, "MovieLens metadata TMDB IDs must be unique")
    eligibility = pd.read_parquet(
        eligibility_catalog_path,
        columns=["movie_id", "blocked", "released_at_origin", "train_count"],
    )
    eligible = eligibility.loc[
        (~eligibility["blocked"])
        & eligibility["released_at_origin"]
        & (eligibility["train_count"] > 0)
    ].copy()
    catalog = (
        eligible.merge(metadata, on="movie_id", validate="one_to_one")
        .merge(service, on="tmdb_id", validate="one_to_one")
    )
    catalog["genres"] = catalog["genre_ids"].map(_genre_tuple)
    catalog["missing_korean_title"] = ~catalog["title"].fillna("").map(
        lambda value: bool(HANGUL_RE.search(str(value)))
    )
    catalog["tmdb_ok"] = (
        catalog["tmdb_vote_average"].map(
            lambda value: pd.notna(value) and 0 < float(value) <= 10
        )
        & catalog["tmdb_vote_count"].map(_integer_vote_count)
    )

    kobis = pd.read_parquet(
        kobis_path,
        columns=[
            "match_status", "service_movie_id", "tmdb_id", "kobis_audience_cumulative",
        ],
    )
    verified = kobis.loc[kobis["match_status"].eq("MATCHED")].copy()
    verified = verified.dropna(subset=["service_movie_id", "tmdb_id"])
    verified["service_movie_id"] = verified["service_movie_id"].astype("int64")
    verified["tmdb_id"] = verified["tmdb_id"].astype("int64")
    require(
        not verified.duplicated(["service_movie_id", "tmdb_id"]).any(),
        "verified KOBIS identities must be unique",
    )
    verified["kobis_ok"] = (
        pd.to_numeric(verified["kobis_audience_cumulative"], errors="coerce") >= 10_000
    )
    catalog = catalog.merge(
        verified[["service_movie_id", "tmdb_id", "kobis_audience_cumulative", "kobis_ok"]],
        on=["service_movie_id", "tmdb_id"], how="left", validate="one_to_one",
    )
    catalog["kobis_ok"] = catalog["kobis_ok"].eq(True)
    catalog["weak_public_evidence"] = ~(catalog["tmdb_ok"] | catalog["kobis_ok"])
    catalog["priority_tier"] = (
        catalog["missing_korean_title"].astype("int8")
        + catalog["weak_public_evidence"].astype("int8")
    )
    return catalog.sort_values("service_movie_id").reset_index(drop=True)


def percentile(values: np.ndarray) -> np.ndarray:
    return pd.Series(np.asarray(values, dtype=np.float64)).rank(
        method="average", pct=True
    ).to_numpy(np.float64)


@dataclass(frozen=True)
class ModelInputs:
    factor_by_movie: dict[int, np.ndarray]
    genre_axis: tuple[int, ...]
    candidate_genres: np.ndarray
    global_mean: float
    regularization: float = 0.1


def load_model_inputs(catalog: pd.DataFrame, als_factors_path: Path) -> ModelInputs:
    artifact = np.load(als_factors_path)
    movie_ids = artifact["movie_ids"].astype(np.int64)
    factors = artifact["movie_factors"].astype(np.float64)
    require(len(movie_ids) == len(factors), "ALS factor axes must align")
    require(len(np.unique(movie_ids)) == len(movie_ids), "ALS movie IDs must be unique")
    factor_by_movie = {int(movie_id): factor for movie_id, factor in zip(movie_ids, factors)}

    genre_axis = tuple(sorted({genre for values in catalog["genres"] for genre in values}))
    genre_index = {genre: index for index, genre in enumerate(genre_axis)}
    candidate_genres = np.zeros((len(catalog), len(genre_axis)), dtype=np.float64)
    for row_index, values in enumerate(catalog["genres"]):
        for genre in values:
            candidate_genres[row_index, genre_index[genre]] = 1.0
    norms = np.linalg.norm(candidate_genres, axis=1)
    candidate_genres = np.divide(
        candidate_genres, norms[:, None], out=np.zeros_like(candidate_genres), where=norms[:, None] > 0,
    )
    return ModelInputs(factor_by_movie, genre_axis, candidate_genres, global_mean=3.5)


def _content_scores(
    history: pd.DataFrame,
    candidate_indices: np.ndarray,
    model_inputs: ModelInputs,
    popularity_pct: np.ndarray,
) -> np.ndarray:
    if history.empty:
        return popularity_pct.copy()
    genre_index = {genre: index for index, genre in enumerate(model_inputs.genre_axis)}
    profile = np.zeros(len(model_inputs.genre_axis), dtype=np.float64)
    ratings = history["score"].to_numpy(np.float64)
    anchor = (ratings.sum() + 5.0 * 3.5) / (len(ratings) + 5.0)
    for row in history.itertuples(index=False):
        for genre in row.genres:
            profile[genre_index[genre]] += float(row.score) - anchor
    norm = np.linalg.norm(profile)
    if norm <= 1e-12:
        return popularity_pct.copy()
    return model_inputs.candidate_genres[candidate_indices] @ (profile / norm)


def _als_scores(
    history: pd.DataFrame,
    candidate_movie_ids: np.ndarray,
    model_inputs: ModelInputs,
    popularity_pct: np.ndarray,
) -> np.ndarray:
    observed = [
        (model_inputs.factor_by_movie.get(int(row.movie_id)), float(row.score))
        for row in history.itertuples(index=False)
    ]
    observed = [(factor, score) for factor, score in observed if factor is not None]
    if not observed:
        return popularity_pct.copy()
    matrix = np.vstack([factor for factor, _ in observed])
    ratings = np.asarray([score for _, score in observed], dtype=np.float64)
    gram = matrix.T @ matrix
    ridge = model_inputs.regularization * len(observed) * np.eye(matrix.shape[1])
    user_factor = np.linalg.solve(gram + ridge, matrix.T @ ratings)
    result = np.empty(len(candidate_movie_ids), dtype=np.float64)
    for index, movie_id in enumerate(candidate_movie_ids):
        factor = model_inputs.factor_by_movie.get(int(movie_id))
        result[index] = factor @ user_factor if factor is not None else popularity_pct[index]
    return result


def score_local_models(
    history: pd.DataFrame,
    catalog: pd.DataFrame,
    candidate_indices: np.ndarray,
    model_inputs: ModelInputs,
) -> dict[str, np.ndarray]:
    candidate = catalog.iloc[candidate_indices]
    popularity = np.log1p(candidate["train_count"].to_numpy(np.float64))
    popularity_pct = percentile(popularity)
    content = _content_scores(history, candidate_indices, model_inputs, popularity_pct)
    als = _als_scores(
        history, candidate["movie_id"].to_numpy(np.int64), model_inputs, popularity_pct
    )
    blend = 0.8 * popularity_pct + 0.2 * percentile(als)
    return {
        "POPULAR_COUNT": popularity,
        "CONTENT_GENRE": content,
        "ALS_FOLDIN": als,
        "POPULAR_80_ALS_20": blend,
    }


def apply_display_policy(candidate: pd.DataFrame, raw_score: np.ndarray) -> pd.DataFrame:
    require(len(candidate) == len(raw_score), "candidate and score axes must align")
    require(np.isfinite(raw_score).all(), "raw scores must be finite")
    result = candidate.copy()
    result["raw_score"] = np.asarray(raw_score, dtype=np.float64)
    service_ids = result["service_movie_id"].to_numpy(np.int64)
    tiers = result["priority_tier"].to_numpy(np.int8)
    raw_order = np.lexsort((service_ids, -result["raw_score"].to_numpy()))
    raw_rank = np.empty(len(result), dtype=np.int64)
    raw_rank[raw_order] = np.arange(1, len(result) + 1)
    result["raw_rank"] = raw_rank
    result["raw_percentile"] = percentile(result["raw_score"].to_numpy())
    result["adjusted_score"] = result["raw_percentile"] - tiers
    adjusted_order = np.lexsort((service_ids, -result["raw_score"].to_numpy(), tiers))
    adjusted_rank = np.empty(len(result), dtype=np.int64)
    adjusted_rank[adjusted_order] = np.arange(1, len(result) + 1)
    result["adjusted_rank"] = adjusted_rank
    result["adjustment_reason"] = [
        "+".join(
            reason for enabled, reason in (
                (bool(no_title), "NO_KOREAN_TITLE"),
                (bool(weak), "NO_STRONG_TMDB_OR_KOBIS"),
            ) if enabled
        ) or "NONE"
        for no_title, weak in zip(
            result["missing_korean_title"], result["weak_public_evidence"], strict=True
        )
    ]
    result["policy_version"] = POLICY_VERSION
    return result


def _k_specs(user_rows: pd.DataFrame) -> list[tuple[str, int]]:
    count = len(user_rows)
    specs = [(str(k), k) for k in K_VALUES if k <= count]
    if count not in K_VALUES:
        specs.append(("FULL", count))
    return specs


def _prepare_history(prefix: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    mapped = mapping[["service_movie_id", "movie_id", "genres"]].rename(
        columns={"movie_id": "movielens_movie_id"}
    )
    history = prefix.merge(
        mapped,
        left_on="movie_id", right_on="service_movie_id", how="inner", validate="many_to_one",
    )
    return history[["score", "genres", "movielens_movie_id"]].rename(
        columns={"movielens_movie_id": "movie_id"}
    )


def build_recommendations(
    ratings: pd.DataFrame,
    catalog: pd.DataFrame,
    model_inputs: ModelInputs,
    top_n: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    outputs: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, object]] = []
    all_indices = np.arange(len(catalog), dtype=np.int64)
    for user_id, user_rows in ratings.groupby("user_id", sort=True):
        user_rows = user_rows.sort_values(["created_at", "movie_id", "input_row"], kind="mergesort")
        rated_ids = set(user_rows["movie_id"].astype(int))
        candidate_mask = ~catalog["service_movie_id"].isin(rated_ids).to_numpy()
        candidate_indices = all_indices[candidate_mask]
        candidate = catalog.iloc[candidate_indices].reset_index(drop=True)
        nickname = str(user_rows["nickname"].iloc[0])
        for k_label, k_used in _k_specs(user_rows):
            prefix = user_rows.iloc[:k_used]
            history = _prepare_history(prefix, catalog)
            scores = score_local_models(history, catalog, candidate_indices, model_inputs)
            coverage_rows.append({
                "user_id": int(user_id), "nickname": nickname, "k_label": k_label,
                "k_requested": int(k_used), "k_mapped": int(len(history)),
                "candidate_count": int(len(candidate)),
            })
            for model, raw_score in scores.items():
                ranked = apply_display_policy(candidate, raw_score)
                selected = ranked.loc[
                    (ranked["raw_rank"] <= top_n) | (ranked["adjusted_rank"] <= top_n)
                ].copy()
                selected.insert(0, "model", model)
                selected.insert(0, "k_mapped", int(len(history)))
                selected.insert(0, "k_requested", int(k_used))
                selected.insert(0, "k_label", k_label)
                selected.insert(0, "nickname", nickname)
                selected.insert(0, "user_id", int(user_id))
                selected["is_raw_top_n"] = selected["raw_rank"] <= top_n
                selected["is_adjusted_top_n"] = selected["adjusted_rank"] <= top_n
                outputs.append(selected)
    return pd.concat(outputs, ignore_index=True), pd.DataFrame(coverage_rows)


def append_external_scores(
    recommendations: pd.DataFrame,
    ratings: pd.DataFrame,
    catalog: pd.DataFrame,
    paths: Iterable[Path],
    top_n: int,
) -> pd.DataFrame:
    outputs = [recommendations]
    for path in paths:
        external = pd.read_csv(path)
        required = {"user_id", "k_label", "model", "service_movie_id", "raw_score"}
        require(required.issubset(external.columns), f"external score columns missing in {path}")
        for (user_id, k_label, model), group in external.groupby(
            ["user_id", "k_label", "model"], sort=True
        ):
            user_rows = ratings.loc[ratings["user_id"].eq(int(user_id))]
            require(not user_rows.empty, f"external score user is absent: {user_id}")
            rated_ids = set(user_rows["movie_id"].astype(int))
            merged = catalog.merge(
                group[["service_movie_id", "raw_score"]],
                on="service_movie_id", how="inner", validate="one_to_one",
            )
            merged = merged.loc[~merged["service_movie_id"].isin(rated_ids)].reset_index(drop=True)
            ranked = apply_display_policy(merged.drop(columns="raw_score"), merged["raw_score"].to_numpy())
            selected = ranked.loc[
                (ranked["raw_rank"] <= top_n) | (ranked["adjusted_rank"] <= top_n)
            ].copy()
            selected.insert(0, "model", str(model))
            selected.insert(0, "k_mapped", pd.NA)
            selected.insert(0, "k_requested", pd.NA)
            selected.insert(0, "k_label", str(k_label))
            selected.insert(0, "nickname", str(user_rows["nickname"].iloc[0]))
            selected.insert(0, "user_id", int(user_id))
            selected["is_raw_top_n"] = selected["raw_rank"] <= top_n
            selected["is_adjusted_top_n"] = selected["adjusted_rank"] <= top_n
            outputs.append(selected)
    return pd.concat(outputs, ignore_index=True)


def build_wide_comparison(recommendations: pd.DataFrame) -> pd.DataFrame:
    adjusted = recommendations.loc[recommendations["is_adjusted_top_n"]].copy()
    adjusted["recommendation"] = [
        f"{title} [id={int(movie_id)}] · raw#{int(raw_rank)} · tier={int(tier)} · {reason}"
        for title, movie_id, raw_rank, tier, reason in zip(
            adjusted["title"], adjusted["service_movie_id"], adjusted["raw_rank"],
            adjusted["priority_tier"], adjusted["adjustment_reason"], strict=True,
        )
    ]
    index = ["user_id", "nickname", "k_requested", "k_label", "adjusted_rank"]
    wide = adjusted.pivot(index=index, columns="model", values="recommendation").reset_index()
    wide.columns.name = None
    model_columns = sorted(column for column in wide.columns if column not in index)
    return wide[index + model_columns].sort_values(
        ["user_id", "k_requested", "k_label", "adjusted_rank"], kind="mergesort"
    )


def render_html(wide: pd.DataFrame, top_n: int) -> str:
    model_columns = [
        column for column in wide.columns
        if column not in {"user_id", "nickname", "k_requested", "k_label", "adjusted_rank"}
    ]
    blocks: list[str] = []
    for user_id, user_frame in wide.groupby("user_id", sort=True):
        nickname = html.escape(str(user_frame["nickname"].iloc[0]))
        k_blocks: list[str] = []
        for (k_requested, k_label), k_frame in user_frame.groupby(
            ["k_requested", "k_label"], sort=True, dropna=False
        ):
            rows: list[str] = []
            for _, row in k_frame.sort_values("adjusted_rank").iterrows():
                cells = "".join(
                    f"<td>{html.escape(str(row[model]))}</td>" for model in model_columns
                )
                rows.append(f"<tr><td>{int(row['adjusted_rank'])}</td>{cells}</tr>")
            headers = "".join(f"<th>{html.escape(model)}</th>" for model in model_columns)
            k_blocks.append(
                f"<details><summary>K={html.escape(str(k_label))} (입력 {k_requested}개)</summary>"
                f"<section><table><thead><tr><th>조정 순위</th>{headers}</tr></thead><tbody>"
                + "".join(rows) + "</tbody></table></section></details>"
            )
        blocks.append(f"<h2>{nickname} <small>user_id={int(user_id)}</small></h2>" + "".join(k_blocks))
    return f"""<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">
<title>S15P21E106-625/628 추천 비교</title><style>
body{{font-family:system-ui,sans-serif;margin:24px;line-height:1.45;color:#202124}}
table{{border-collapse:collapse;width:100%;margin:8px 0 20px}}th,td{{border:1px solid #ddd;padding:6px 8px;text-align:left}}
th{{background:#f3f4f6;position:sticky;top:0}}details{{margin:12px 0}}summary{{font-size:1.1rem;font-weight:700;cursor:pointer}}
section{{overflow:auto}}small{{font-weight:400;color:#666}}code{{background:#f3f4f6;padding:2px 4px}}
</style></head><body><h1>S15P21E106-625/628 서비스 사용자 추천 비교</h1>
<p>상위 {top_n}개. 원점수는 보존했고, <code>{POLICY_VERSION}</code>을 최종 표시 순위에만 적용했습니다.</p>
<p>tier 0: 감점 사유 없음 · tier 1: 한 사유 · tier 2: 두 사유. 사용자가 이미 평가한 영화는 표시 후보에서 제외했습니다.</p>
{''.join(blocks)}</body></html>"""


def _json_safe(value: object) -> object:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    raise TypeError(type(value).__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--service-catalog", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--eligibility-catalog", type=Path, required=True)
    parser.add_argument("--als-factors", type=Path, required=True)
    parser.add_argument("--kobis", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--external-scores", type=Path, action="append", default=[])
    parser.add_argument("--top-n", type=int, default=10)
    args = parser.parse_args()
    require(args.top_n > 0, "top-n must be positive")
    inputs = [
        args.ratings, args.service_catalog, args.metadata, args.eligibility_catalog,
        args.als_factors, args.kobis, *args.external_scores,
    ]
    for path in inputs:
        require(path.is_file(), f"input not found: {path}")

    ratings = parse_markdown_ratings(args.ratings)
    catalog = build_catalog(
        args.service_catalog, args.metadata, args.eligibility_catalog, args.kobis
    )
    model_inputs = load_model_inputs(catalog, args.als_factors)
    recommendations, coverage = build_recommendations(
        ratings, catalog, model_inputs, args.top_n
    )
    if args.external_scores:
        recommendations = append_external_scores(
            recommendations, ratings, catalog, args.external_scores, args.top_n
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    recommendations_path = args.output_dir / "recommendations.csv"
    coverage_path = args.output_dir / "history-coverage.csv"
    wide_path = args.output_dir / "comparison-wide.csv"
    html_path = args.output_dir / "comparison.html"
    wide = build_wide_comparison(recommendations)
    recommendations.to_csv(recommendations_path, index=False, encoding="utf-8-sig")
    coverage.to_csv(coverage_path, index=False, encoding="utf-8-sig")
    wide.to_csv(wide_path, index=False, encoding="utf-8-sig")
    html_path.write_text(render_html(wide, args.top_n), encoding="utf-8")

    adjusted = recommendations.loc[recommendations["is_adjusted_top_n"]]
    list_keys = ["user_id", "k_label", "model"]
    adjusted_sizes = adjusted.groupby(list_keys, dropna=False).size()
    require(adjusted_sizes.eq(args.top_n).all(), "every adjusted list must contain top-n rows")
    require(
        adjusted.groupby(list_keys, dropna=False)["service_movie_id"].nunique().eq(args.top_n).all(),
        "adjusted lists must not contain duplicate movies",
    )
    require(
        adjusted.groupby(list_keys, dropna=False)["adjusted_rank"].apply(
            lambda values: sorted(values.astype(int)) == list(range(1, args.top_n + 1))
        ).all(),
        "adjusted ranks must be consecutive",
    )
    rated_by_user = ratings.groupby("user_id")["movie_id"].apply(lambda values: set(values.astype(int)))
    require(
        all(
            int(row.service_movie_id) not in rated_by_user.loc[int(row.user_id)]
            for row in recommendations.itertuples(index=False)
        ),
        "a supplied user rating leaked into displayed recommendations",
    )
    manifest = {
        "schema_version": 1,
        "jira_scope": ["S15P21E106-625", "S15P21E106-628", "S15P21E106-668"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "LOCAL_REVIEW_ONLY",
        "protocol": {
            "k_values": list(K_VALUES), "full_when_non_duplicate": True,
            "history_order": "created_at ASC, movie_id ASC",
            "rated_movie_policy": "exclude every supplied rating from displayed candidates",
            "display_policy": POLICY_VERSION,
            "model_arms": sorted(recommendations["model"].unique().tolist()),
            "top_n": args.top_n,
        },
        "pending_arms": {
            "GBT_622": "WAITING_FOR_SERVICE_USER_RAW_SCORE_EXPORT_FROM_V16",
            "SPARSE_HISTORY_CONTENT_FM_623": "WAITING_FOR_SERVICE_USER_RAW_SCORE_EXPORT",
        },
        "counts": {
            "ratings": len(ratings), "users": ratings["user_id"].nunique(),
            "catalog_candidates": len(catalog),
            "catalog_missing_korean_title": int(catalog["missing_korean_title"].sum()),
            "catalog_weak_public_evidence": int(catalog["weak_public_evidence"].sum()),
            "adjusted_top_rows": len(adjusted),
            "adjusted_top_missing_korean_title": int(adjusted["missing_korean_title"].sum()),
            "adjusted_top_weak_public_evidence": int(adjusted["weak_public_evidence"].sum()),
        },
        "inputs": {
            str(path.resolve()): {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in inputs
        },
        "artifacts": {},
        "validation": {
            "status": "PASS",
            "adjusted_lists_have_exact_top_n": True,
            "adjusted_lists_have_unique_movies": True,
            "adjusted_ranks_are_consecutive": True,
            "supplied_ratings_absent_from_displayed_candidates": True,
            "raw_scores_preserved": True,
        },
        "limitations": [
            "GBT and FM are included only when raw score exports are supplied.",
            "This is a recommendation-list inspection artifact, not an offline metric or promotion decision.",
            "KOBIS is counted only for verified MATCHED service/TMDB identities.",
        ],
    }
    manifest_path = args.output_dir / "manifest.json"
    for path in (recommendations_path, coverage_path, wide_path, html_path):
        manifest["artifacts"][path.name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_safe) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "users": int(ratings["user_id"].nunique()),
        "ratings": len(ratings),
        "catalog_candidates": len(catalog),
        "models": sorted(recommendations["model"].unique().tolist()),
        "recommendation_rows": len(recommendations),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
