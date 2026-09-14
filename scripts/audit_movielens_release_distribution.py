"""Whole MovieLens observation distribution; never load rating values or E labels."""

from __future__ import annotations

import json
import re
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from feelm_preference_structure import pin, require, write_json

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/research/movielens-release-distribution"
OUT = ROOT / "outputs/recommendation-evidence/movielens-release-distribution"
ARCHIVE = Path("C:/higher/projects/MM/data/raw/ml-32m.zip")
REC045 = ROOT / "outputs/recommendation-evidence/rec-ev-045"
ARCHIVE_PIN = {
    "bytes": 238950008,
    "sha256": "e4a68655d7386b8f95f2f2424b2ff975dfdd15ffd59e0d864a14dca43e99d6ee",
}


def year_from_title(title):
    match = re.search(r"\((\d{4})\)\s*$", str(title))
    if not match:
        return -1
    value = int(match.group(1))
    return value if 1800 <= value <= 2026 else -1


def share_top(counts, fraction):
    number = max(1, int(np.ceil(len(counts) * fraction)))
    return (
        float(np.sort(counts)[-number:].sum() / counts.sum()) if counts.sum() else None
    )


def eval_distribution(lookup, movie_year_index, number_years):
    inputs = []
    frequency = {}
    for r in range(3):
        with np.load(REC045 / f"r{r}/input.npz", allow_pickle=False) as z:
            data = {k: z[k] for k in ("movie_ids", "user_keys", "e_index", "e_offsets")}
        inputs.append(data)
        for key in data["user_keys"]:
            frequency[str(key)] = frequency.get(str(key), 0) + 1
    observations = np.zeros(number_years, dtype=np.int64)
    unique_pairs = np.zeros(number_years, dtype=np.int64)
    weights = np.zeros(number_years)
    seen = set()
    unique_movies = set()
    rounds = []
    for r, data in enumerate(inputs):
        mids = data["movie_ids"][data["e_index"]]
        positions = lookup[mids]
        require((positions >= 0).all(), "evaluation movie missing from MovieLens")
        yr = movie_year_index[positions]
        observations += np.bincount(yr, minlength=number_years)
        for u, key in enumerate(data["user_keys"]):
            rows = slice(data["e_offsets"][u], data["e_offsets"][u + 1])
            size = rows.stop - rows.start
            require(size > 0, "empty E")
            w = 1 / (len(frequency) * frequency[str(key)] * size)
            weights += np.bincount(
                yr[rows], weights=np.full(size, w), minlength=number_years
            )
            for mid, yidx in zip(mids[rows], yr[rows], strict=True):
                pair = (str(key), int(mid))
                if pair not in seen:
                    seen.add(pair)
                    unique_pairs[yidx] += 1
                unique_movies.add(int(mid))
        rounds.append(
            {"round": r, "users": len(data["user_keys"]), "E_observations": len(mids)}
        )
    require(np.isclose(weights.sum(), 1, atol=1e-12), "evaluation weights")
    movie_counts = np.bincount(
        movie_year_index[lookup[np.array(sorted(unique_movies))]],
        minlength=number_years,
    )
    return dict(
        observations=observations,
        unique_pairs=unique_pairs,
        metric_weights=weights,
        unique_movies=movie_counts,
    ), {
        "unique_users": len(frequency),
        "unique_user_movie_pairs": len(seen),
        "unique_movies": len(unique_movies),
        "rounds": rounds,
        "weight_sum": float(weights.sum()),
        "weight_formula": "1 / unique_users / appearances_of_user_across_rounds / E_pairs_in_user_round",
    }


def fingerprint():
    return {"script": pin(__file__), "design": pin(DOC / "README.md")}


def main():
    review = json.loads((DOC / "execution-review.json").read_text(encoding="utf-8"))
    require(
        review["status"] == "PASS" and review["fingerprint"] == fingerprint(),
        "independent audit pre-review",
    )
    require(pin(ARCHIVE) == ARCHIVE_PIN, "archive drift")
    prior_seal = json.loads(
        (REC045 / "completion-seal.json").read_text(encoding="utf-8")
    )
    require(
        pin(REC045 / "prediction-seal.json")
        == prior_seal["outputs"]["prediction-seal.json"],
        "prior source ledger drift",
    )
    prior_sources = json.loads(
        (REC045 / "prediction-seal.json").read_text(encoding="utf-8")
    )["source_pins"]
    tmdb_path = (
        ROOT
        / "outputs/recommendation-evidence/rec-ev-027-catalog/structured-features.parquet"
    )
    tmdb_pin = pin(tmdb_path)
    require(
        tmdb_pin == prior_sources[tmdb_path.relative_to(ROOT).as_posix()],
        "TMDB source drift before read",
    )
    for r in range(3):
        name = f"r{r}/input.npz"
        require(pin(REC045 / name) == prior_seal["outputs"][name], "prior input drift")
    require(not OUT.exists(), "preserve existing distribution audit")
    OUT.mkdir(parents=True)
    started = time.monotonic()
    try:
        with zipfile.ZipFile(ARCHIVE) as bundle:
            movies = pd.read_csv(bundle.open("ml-32m/movies.csv"))
            source_readme = bundle.read("ml-32m/README.txt").decode("utf-8")
            write_json(
                OUT / "source-readme.json",
                {"text": source_readme, "archive": ARCHIVE_PIN},
            )
        require(
            len(movies) == 87585 and movies.movieId.is_unique, "MovieLens movie axis"
        )
        movies = movies.sort_values("movieId").reset_index(drop=True)
        ids = movies.movieId.to_numpy(dtype=np.int64)
        years = movies.title.map(year_from_title).to_numpy(dtype=np.int16)
        year_axis = np.array([-1, *range(int(years[years > 0].min()), 2027)])
        yi = np.searchsorted(year_axis, years)
        lookup = np.full(int(ids.max()) + 1, -1, dtype=np.int32)
        lookup[ids] = np.arange(len(ids))
        n_movies = np.bincount(yi, minlength=len(year_axis))
        count_movie = np.zeros(len(movies), dtype=np.int64)
        user_total = np.zeros(200949, dtype=np.int64)
        user_year_seen = np.zeros((200949, len(year_axis)), dtype=bool)
        user_year_count = np.zeros((200949, len(year_axis)), dtype=np.int32)
        entry_axis = np.arange(1990, 2027)
        boundaries = np.array(
            [
                np.datetime64(f"{year}-01-01", "s").astype(np.int64)
                for year in range(1990, 2028)
            ]
        )
        joint = np.zeros((len(year_axis), len(entry_axis)), dtype=np.int64)
        total = 0
        minimum = np.iinfo(np.int64).max
        maximum = 0
        with (
            zipfile.ZipFile(ARCHIVE) as bundle,
            bundle.open("ml-32m/ratings.csv") as stream,
        ):
            for chunk in pd.read_csv(
                stream,
                usecols=["userId", "movieId", "timestamp"],
                dtype={"userId": "int32", "movieId": "int32", "timestamp": "int64"},
                chunksize=500000,
            ):
                users = chunk.userId.to_numpy()
                mids = chunk.movieId.to_numpy()
                stamp = chunk.timestamp.to_numpy()
                require(
                    users.min() > 0
                    and users.max() <= 200948
                    and mids.min() >= 0
                    and mids.max() < len(lookup),
                    "raw ID bounds",
                )
                idx = lookup[mids]
                require((idx >= 0).all(), "rating movie not in catalog")
                yr = yi[idx]
                entry = np.searchsorted(boundaries, stamp, side="right") - 1
                require(
                    (entry >= 0).all() and (entry < len(entry_axis)).all(),
                    "timestamp bounds",
                )
                count_movie += np.bincount(idx, minlength=len(movies))
                user_total += np.bincount(users, minlength=len(user_total))
                user_year_seen[users, yr] = True
                np.add.at(user_year_count, (users, yr), 1)
                joint += np.bincount(
                    yr * len(entry_axis) + entry, minlength=joint.size
                ).reshape(joint.shape)
                minimum, maximum = (
                    min(minimum, int(stamp.min())),
                    max(maximum, int(stamp.max())),
                )
                total += len(chunk)
                if total % 5000000 == 0:
                    print(
                        f"counted {total:,} rating records; rating values excluded",
                        flush=True,
                    )
                require(time.monotonic() - started < 900, "15 minute cap")
        require(
            total == 32000204 and joint.sum() == total and count_movie.sum() == total,
            "whole dataset counts",
        )
        active = user_total > 0
        require(int(active.sum()) == 200948, "whole dataset unique users")
        whole_user_equal = (user_year_count[active] / user_total[active, None]).mean(
            axis=0
        )
        require(
            np.array_equal(user_year_count.sum(axis=1), user_total),
            "user-year count totals",
        )
        require(
            np.isclose(whole_user_equal.sum(), 1, atol=1e-12), "whole-user weight sum"
        )
        eval_data, eval_summary = eval_distribution(lookup, yi, len(year_axis))
        rows = []
        for j, year in enumerate(year_axis):
            mask = yi == j
            values = count_movie[mask]
            rated = int((values > 0).sum())
            rows.append(
                {
                    "release_year": int(year),
                    "catalog_movies": int(mask.sum()),
                    "rated_movies": rated,
                    "rating_records": int(joint[j].sum()),
                    "unique_raters": int(user_year_seen[:, j].sum()),
                    "movie_share": float(n_movies[j] / len(movies)),
                    "rating_share": float(joint[j].sum() / total),
                    "whole_user_equal_share": float(whole_user_equal[j]),
                    "mean_ratings_per_catalog_movie": float(values.mean())
                    if len(values)
                    else None,
                    "median_ratings_per_catalog_movie": float(np.median(values))
                    if len(values)
                    else None,
                    "p90_ratings_per_catalog_movie": float(np.quantile(values, 0.9))
                    if len(values)
                    else None,
                    "movies_zero_ratings": int((values == 0).sum()),
                    "movies_under10": int((values < 10).sum()),
                    "movies_under50": int((values < 50).sum()),
                    "movies_under100": int((values < 100).sum()),
                    "top1pct_movie_rating_share": share_top(values, 0.01),
                    "top10pct_movie_rating_share": share_top(values, 0.1),
                    "eval_round_observations": int(eval_data["observations"][j]),
                    "eval_unique_pairs": int(eval_data["unique_pairs"][j]),
                    "eval_unique_movies": int(eval_data["unique_movies"][j]),
                    "eval_metric_weight": float(eval_data["metric_weights"][j]),
                }
            )
        year_frame = pd.DataFrame(rows)
        year_frame.to_parquet(OUT / "by-release-year.parquet", index=False)
        movies.assign(release_year=years, rating_records=count_movie).to_parquet(
            OUT / "movie-counts.parquet", index=False
        )
        decades = []
        groups = [("UNKNOWN", years < 0), ("BEFORE_1980", (years > 0) & (years < 1980))]
        groups += [
            (f"{decade}s", (years >= decade) & (years < decade + 10))
            for decade in (1980, 1990, 2000, 2010, 2020)
        ]
        for name, mask in groups:
            selected_years = np.unique(yi[mask])
            rec = year_frame.iloc[selected_years]
            values = count_movie[mask]
            distinct_raters = int(user_year_seen[:, selected_years].any(axis=1).sum())
            decades.append(
                {
                    "group": name,
                    "catalog_movies": int(mask.sum()),
                    "rated_movies": int((values > 0).sum()),
                    "rating_records": int(values.sum()),
                    "unique_raters": distinct_raters,
                    "movie_share": float(mask.mean()),
                    "rating_share": float(values.sum() / total),
                    "whole_user_equal_share": float(rec.whole_user_equal_share.sum()),
                    "median_ratings_per_catalog_movie": float(np.median(values))
                    if len(values)
                    else None,
                    "p90_ratings_per_catalog_movie": float(np.quantile(values, 0.9))
                    if len(values)
                    else None,
                    "movies_zero_ratings": int((values == 0).sum()),
                    "movies_under10": int((values < 10).sum()),
                    "movies_under50": int((values < 50).sum()),
                    "movies_under100": int((values < 100).sum()),
                    "eval_unique_pair_share": float(
                        rec.eval_unique_pairs.sum()
                        / eval_summary["unique_user_movie_pairs"]
                    ),
                    "eval_metric_weight": float(rec.eval_metric_weight.sum()),
                    "top1pct_movie_rating_share": share_top(values, 0.01),
                    "top10pct_movie_rating_share": share_top(values, 0.1),
                }
            )
        tmdb = pd.read_parquet(tmdb_path, columns=["movie_id", "release_year"])
        require(pin(tmdb_path) == tmdb_pin, "TMDB source changed during audit")
        merged = pd.DataFrame(
            {"movie_id": ids, "ml_title_year": years, "rating_records": count_movie}
        ).merge(tmdb, on="movie_id", how="left", validate="one_to_one")
        both = (merged.ml_title_year > 0) & merged.release_year.notna()
        differences = merged.loc[both & (merged.ml_title_year != merged.release_year)]
        coverage = {
            "both_years_present_movies": int(both.sum()),
            "year_mismatch_movies": len(differences),
            "year_mismatch_rating_records": int(differences.rating_records.sum()),
            "ml_unparsed_tmdb_present_movies": int(
                ((merged.ml_title_year < 0) & merged.release_year.notna()).sum()
            ),
            "primary_year_not_overwritten": True,
        }
        np.savez_compressed(
            OUT / "aggregate-counts.npz",
            release_years=year_axis,
            entry_years=entry_axis,
            release_by_entry=joint,
            ratings_per_movie=count_movie,
            catalog_movie_ids=ids,
            unique_raters_per_release_year=user_year_seen.sum(axis=0),
            **eval_data,
        )
        lags = {}
        for j, released in enumerate(year_axis):
            if released < 0:
                continue
            for t, entered in enumerate(entry_axis):
                lag = int(entered - released)
                lags[lag] = lags.get(lag, 0) + int(joint[j, t])
        summary = {
            "status": "DESCRIPTIVE_DISTRIBUTION_AUDIT",
            "movie_rows": len(movies),
            "rating_records": total,
            "unique_raters": int(active.sum()),
            "min_user_ratings": int(user_total[active].min()),
            "timestamp_min_utc": str(np.datetime64(minimum, "s")),
            "timestamp_max_utc": str(np.datetime64(maximum, "s")),
            "title_year_unparsed_movies": int((years < 0).sum()),
            "title_year_unparsed_ratings": int(count_movie[years < 0].sum()),
            "top1pct_movies_rating_share": share_top(count_movie, 0.01),
            "top10pct_movies_rating_share": share_top(count_movie, 0.1),
            "zero_ratings_movies": int((count_movie == 0).sum()),
            "median_ratings_per_movie": float(np.median(count_movie)),
            "rating_entry_years": [
                {"year": int(y), "ratings": int(n)}
                for y, n in zip(entry_axis, joint.sum(axis=0), strict=True)
            ],
            "entry_year_precedes_release_year_ratings": int(
                joint[
                    (year_axis[:, None] > 0)
                    & (entry_axis[None, :] < year_axis[:, None])
                ].sum()
            ),
            "entry_year_minus_release_year": [
                {"calendar_year_difference": lag, "rating_records": count}
                for lag, count in sorted(lags.items())
                if count
            ],
            "decades": decades,
            "years": rows,
            "evaluation": eval_summary,
            "tmdb_year_crosscheck": coverage,
            "rating_values_used": False,
            "E_labels_opened": False,
            "model_training_or_scoring": False,
            "archive": {"path": str(ARCHIVE), **ARCHIVE_PIN},
            "seconds": time.monotonic() - started,
        }
        write_json(OUT / "summary.json", summary)
        write_json(
            OUT / "completion-seal.json",
            {
                "status": "COMPLETE",
                "fingerprint": fingerprint(),
                "archive": ARCHIVE_PIN,
                "rec045_completion": pin(REC045 / "completion-seal.json"),
                "tmdb_metadata": tmdb_pin,
                "outputs": {
                    p.name: pin(p) for p in sorted(OUT.iterdir()) if p.is_file()
                },
            },
        )
        print(
            json.dumps(
                {
                    k: summary[k]
                    for k in (
                        "rating_records",
                        "unique_raters",
                        "timestamp_max_utc",
                        "top1pct_movies_rating_share",
                        "seconds",
                    )
                }
            ),
            flush=True,
        )
    except Exception as error:
        write_json(
            OUT / "failure.json", {"type": type(error).__name__, "reason": str(error)}
        )
        raise


if __name__ == "__main__":
    main()
