"""REC045: train/predict all fixed arms, seal, then open existing development labels."""

# ruff: noqa: E402 -- BLAS threads must be fixed before importing NumPy/SciPy.

from __future__ import annotations

import os

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_name] = "1"

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psutil
from scipy import sparse

from feelm_joint_preferences import (
    BLOCKS,
    METHODS,
    SMALL_METHODS,
    input_selection,
    intervals,
    predict_user,
    shared_fit,
    static_features,
    training_statistics,
    unique_user_means,
)
from feelm_preference_structure import pin, require, write_json

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recommendation/experiments/rec-ev-045"
OUT = ROOT / "outputs/recommendation-evidence/rec-ev-045"
OLD = ROOT / "outputs/recommendation-evidence/rec-ev-032/user-resplits"
START = time.monotonic()


def guard():
    require(time.monotonic() - START < 7200, "two hour execution cap")
    require(psutil.Process().memory_info().rss < 6 * 1024**3, "6GiB memory cap")


def fingerprint():
    paths = [DOC / name for name in ("README.md", "config.json")]
    paths += [
        ROOT / "scripts" / name
        for name in (
            "feelm_joint_preferences.py",
            "rec_ev_045_joint_preferences.py",
            "test_feelm_joint_preferences.py",
            "feelm_residual_taste.py",
            "feelm_preference_structure.py",
        )
    ]
    return {p.relative_to(ROOT).as_posix(): pin(p) for p in paths}


def load_frame():
    catalog = pd.read_parquet(
        ROOT / "outputs/recommendation-evidence/rec-ev-033/metadata.parquet",
        columns=["movie_id"],
    )
    ids = catalog.movie_id.to_numpy()
    require(np.array_equal(ids, np.sort(np.unique(ids))), "catalog order")
    frame = (
        pd.read_parquet(
            ROOT
            / "outputs/recommendation-evidence/rec-ev-027-catalog/structured-features.parquet"
        )
        .set_index("movie_id")
        .loc[ids]
    )
    country = (
        pd.read_parquet(
            ROOT
            / "outputs/recommendation-evidence/rec-ev-027-catalog/domain-projection.parquet"
        )
        .set_index("movie_id")
        .loc[ids]
    )
    extended = (
        pd.read_parquet(
            ROOT
            / "outputs/recommendation-evidence/rec-ev-045-metadata/extended-metadata.parquet"
        )
        .set_index("movie_id")
        .loc[ids]
    )
    frame = frame.join(country[["production_country_codes"]]).join(
        extended.drop(columns="tmdb_id")
    )
    require(frame.index.is_unique and len(frame) == 85517, "metadata axis")
    return frame.reset_index()


def inputs(frame, round_id):
    ids = frame.movie_id.to_numpy(dtype=np.int64)
    lookup = {int(v): i for i, v in enumerate(ids)}
    with np.load(OLD / "splits.npz", allow_pickle=False) as z:
        keys = z["evaluation_user_keys"][round_id]
        train_ids = z["training_user_ids"][round_id]
        eval_ids = z["evaluation_user_ids"][round_id]
    require(not np.intersect1d(train_ids, eval_ids).size, "user separation")
    profile = pd.read_parquet(OLD / "profiles.parquet").set_index("user_key").loc[keys]
    target = pd.read_parquet(OLD / "targets.parquet").set_index("user_key").loc[keys]
    oi = np.array([[lookup[int(v)] for v in row] for row in profile.profile_movie_ids])
    orating = (np.stack(profile.profile_rating_indices) + 1) / 2
    e_index, offsets = [], [0]
    for u, values in enumerate(target.movie_ids):
        valid = sorted(set(int(v) for v in values if int(v) in lookup))
        require(len(valid) == len(values) and len(valid) >= 2, "fixed target coverage")
        indices = [lookup[v] for v in valid]
        require(not np.intersect1d(oi[u], indices).size, "O/E overlap")
        e_index.extend(indices)
        offsets.append(len(e_index))
    require(
        oi.shape == (2180, 30) and all(len(np.unique(row)) == 30 for row in oi), "O30"
    )
    return dict(
        movie_ids=ids,
        user_keys=keys,
        training_ids=train_ids,
        evaluation_ids=eval_ids,
        o_index=oi,
        o_ratings=orating,
        e_index=np.array(e_index),
        e_offsets=np.array(offsets),
    )


def descriptive_metadata(frame, stats):
    table = pd.DataFrame(
        {
            "ml_train_mean": stats["bayes"],
            "ml_log_count": np.log1p(stats["counts"]),
            "ml_variance": stats["shrunk_variance"],
            "release_year": frame.release_year,
            "runtime": frame.runtime_minutes,
            "tmdb_current_mean": frame.tmdb_vote_average,
            "tmdb_current_log_votes": np.log1p(frame.tmdb_vote_count),
            "tmdb_current_log_popularity": np.log1p(frame.tmdb_popularity),
        }
    )

    def overlap_table(a, b):
        counts = {}
        for left, right in zip(a, b, strict=True):
            for x in set(left):
                for y in set(right):
                    key = f"{x}|{y}"
                    counts[key] = counts.get(key, 0) + 1
        return [
            {"pair": k, "movies": v}
            for k, v in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[
                :30
            ]
        ]

    return {
        "catalog_spearman_descriptive": table.corr(method="spearman").to_dict(),
        "nonmissing": table.notna().sum().to_dict(),
        "country_genre_overlap_top30": overlap_table(
            frame.production_country_codes, frame.genre_ids
        ),
        "country_language_overlap_top30": overlap_table(
            frame.production_country_codes, [[x] for x in frame.original_language]
        ),
        "snapshot_votes_are_not_historical_inputs": True,
    }


def predict_round(frame, static, r):
    folder = OUT / f"r{r}"
    folder.mkdir()
    data = inputs(frame, r)
    np.savez_compressed(folder / "input.npz", **data)
    train = pd.read_parquet(OLD / f"r{r}/training.parquet")
    stats, crowd = training_statistics(
        train, data["movie_ids"], data["training_ids"], data["evaluation_ids"]
    )
    del train
    matrices = {**static, "CROWD": crowd}
    correction, diagnostics = shared_fit(matrices, stats)
    stats["shared_correction"] = correction
    np.savez_compressed(folder / "training-statistics.npz", **stats)
    sparse.save_npz(folder / "CROWD.npz", crowd)
    write_json(folder / "shared-fit.json", diagnostics)
    write_json(folder / "metadata-comparison.json", descriptive_metadata(frame, stats))
    print(f"r{r} shared fit complete; no target stars read", flush=True)
    for k in (5, 10, 30):
        for draw in range(1 if k == 30 else 3):
            methods = METHODS if k == 30 else SMALL_METHODS
            pred = np.empty((len(data["e_index"]), len(methods)))
            support = np.empty((len(data["e_index"]), 7), dtype=np.int8)
            selected = np.empty((len(data["user_keys"]), k), dtype=np.int8)
            for u, key in enumerate(data["user_keys"]):
                chosen = input_selection(
                    key, data["movie_ids"][data["o_index"][u]], k, draw
                )
                selected[u] = chosen
                rows = slice(data["e_offsets"][u], data["e_offsets"][u + 1])
                pred[rows], support[rows] = predict_user(
                    matrices,
                    stats["bayes"],
                    correction,
                    data["o_index"][u, chosen],
                    data["o_ratings"][u, chosen],
                    data["e_index"][rows],
                    full=k == 30,
                )
                if u % 250 == 0:
                    guard()
            np.savez_compressed(
                folder / f"k{k}-d{draw}-predictions.npz",
                methods=np.array(methods),
                predictions=pred,
                support=support,
                selected=selected,
            )
            print(
                f"r{r} k{k} draw{draw} predicted {len(pred)} observed target IDs; stars unopened",
                flush=True,
            )
    return {
        "round": r,
        "users": len(data["user_keys"]),
        "E_pairs": len(data["e_index"]),
        "training_rows": int(stats["training_rows"]),
        "training_users": int(stats["training_users"]),
    }


def strata(frame, data, stats, support):
    ei = data["e_index"]
    n = stats["counts"][ei]
    years = frame.release_year.to_numpy()[ei]
    language = frame.original_language.to_numpy()[ei]
    korean = np.array(["KR" in values for values in frame.production_country_codes])[ei]
    known_country = np.array(
        [len(values) > 0 for values in frame.production_country_codes]
    )[ei]
    masks = {
        "ratings_0": n == 0,
        "ratings_1_49": (n > 0) & (n < 50),
        "ratings_50_499": (n >= 50) & (n < 500),
        "ratings_500plus": n >= 500,
        "language_en": language == "en",
        "language_other": language != "en",
        "production_KR": korean,
        "production_known_non_KR": known_country & ~korean,
        "production_unknown": ~known_country,
        "release_pre1980": years < 1980,
    }
    for year in (1980, 1990, 2000, 2010, 2020):
        masks[f"release_{year}s"] = (years >= year) & (years < year + 10)
    for col, name in ((4, "director"), (5, "cast"), (6, "company_collection")):
        masks[f"{name}_O_matches_0"] = support[:, col] == 0
        masks[f"{name}_O_matches_1"] = support[:, col] == 1
        masks[f"{name}_O_matches_2plus"] = support[:, col] >= 2
    return masks


def evaluate(frame):
    # The sole target-star reader, reached only after all 21 prediction files are sealed.
    sealed = json.loads((OUT / "prediction-seal.json").read_text(encoding="utf-8"))
    require(sealed["fingerprint"] == fingerprint(), "prediction code/design drift")
    for path, expected in sealed["outputs"].items():
        require(pin(OUT / path) == expected, "prediction seal drift: " + path)
    for path, expected in sealed["source_pins"].items():
        require(pin(ROOT / path) == expected, "source changed before scoring: " + path)
    labels = pd.read_parquet(
        OLD / "evaluation-labels.parquet",
        columns=["user_key", "movie_id", "rating_raw"],
    )
    require(
        not labels.duplicated(["user_key", "movie_id"]).any(), "unique target labels"
    )
    labelmap = {
        (row.user_key, int(row.movie_id)): float(row.rating_raw)
        for row in labels.itertuples(index=False)
    }
    require(np.isin(labels.rating_raw, np.arange(1, 11) / 2).all(), "target half stars")
    round_records, all_keys, all_contrasts, all_method_metrics = [], [], [], []
    subgroup_records = []
    for r in range(3):
        folder = OUT / f"r{r}"
        with np.load(folder / "input.npz", allow_pickle=False) as z:
            data = {k: z[k] for k in z.files}
        with np.load(folder / "training-statistics.npz", allow_pickle=False) as z:
            stats = {k: z[k] for k in z.files}
        raw = np.empty(len(data["e_index"]))
        for u, key in enumerate(data["user_keys"]):
            rows = slice(data["e_offsets"][u], data["e_offsets"][u + 1])
            raw[rows] = [
                labelmap[(key, int(data["movie_ids"][i]))]
                for i in data["e_index"][rows]
            ]
        np.savez_compressed(folder / "opened-labels.npz", rating_raw=raw)
        metrics, maemetrics = {}, {}
        for k in (5, 10, 30):
            m = len(METHODS if k == 30 else SMALL_METHODS)
            mse = np.zeros((len(data["user_keys"]), m))
            mae = np.zeros_like(mse)
            draws = 1 if k == 30 else 3
            for d in range(draws):
                with np.load(
                    folder / f"k{k}-d{d}-predictions.npz", allow_pickle=False
                ) as z:
                    err = z["predictions"] - raw[:, None]
                    support = z["support"]
                for u in range(len(data["user_keys"])):
                    rows = slice(data["e_offsets"][u], data["e_offsets"][u + 1])
                    mse[u] += np.mean(err[rows] ** 2, axis=0) / draws
                    mae[u] += np.mean(np.abs(err[rows]), axis=0) / draws
                if k == 30:
                    for name, mask in strata(frame, data, stats, support).items():
                        for u, key in enumerate(data["user_keys"]):
                            rows = slice(data["e_offsets"][u], data["e_offsets"][u + 1])
                            take = mask[rows]
                            if take.any():
                                subgroup_records.append(
                                    {
                                        "round": r,
                                        "user_key": key,
                                        "group": name,
                                        "pairs": int(take.sum()),
                                        "mse": np.mean(
                                            err[rows][take] ** 2, axis=0
                                        ).tolist(),
                                    }
                                )
            metrics[k], maemetrics[k] = mse, mae
        contrasts = np.column_stack(
            [metrics[k][:, 1] - metrics[k][:, 3] for k in (5, 10, 30)]
            + [metrics[30][:, 2] - metrics[30][:, 3]]
            + [metrics[30][:, 4 + b] - metrics[30][:, 3] for b in range(8)]
            + [metrics[30][:, 3] - metrics[30][:, -1]]
        )
        np.savez_compressed(
            folder / "evaluation.npz",
            user_keys=data["user_keys"],
            contrasts=contrasts,
            **{f"mse_k{k}": v for k, v in metrics.items()},
            **{f"mae_k{k}": v for k, v in maemetrics.items()},
        )
        all_keys.extend(data["user_keys"])
        all_contrasts.append(contrasts)
        all_method_metrics.append(np.column_stack([metrics[k] for k in (5, 10, 30)]))
        round_records.append(
            {
                "round": r,
                "mse": {str(k): v.mean(axis=0).tolist() for k, v in metrics.items()},
                "mae": {str(k): v.mean(axis=0).tolist() for k, v in maemetrics.items()},
                "contrasts": contrasts.mean(axis=0).tolist(),
            }
        )
    unique, delta = unique_user_means(np.array(all_keys), np.vstack(all_contrasts))
    _, metric_mean = unique_user_means(
        np.array(all_keys), np.vstack(all_method_metrics)
    )
    ci = intervals(delta)
    names = (
        [f"SHARED_vs_FULL_K{k}" for k in (5, 10, 30)]
        + ["GENRE_vs_FULL_K30"]
        + [f"ADD_{name}_K30" for name in BLOCKS]
        + ["ADD_PERSON_ERA_K30"]
    )
    comparisons = [
        {
            "name": name,
            "gain": float(delta[:, j].mean()),
            "interval": ci[j].tolist(),
            "direction": "IMPROVED"
            if ci[j, 0] > 0
            else "WORSENED"
            if ci[j, 1] < 0
            else "UNDECIDED",
            "round_gains": [record["contrasts"][j] for record in round_records],
        }
        for j, name in enumerate(names)
    ]
    subframe = pd.DataFrame(subgroup_records)
    subframe.to_parquet(OUT / "subgroup-user-metrics.parquet", index=False)
    groups = []
    for name, rows in subframe.groupby("group", sort=True):
        _, values = unique_user_means(rows.user_key.to_numpy(), np.stack(rows.mse))
        effect = values[:, 1] - values[:, 3]
        se = effect.std(ddof=1) / np.sqrt(len(effect)) if len(effect) > 1 else None
        groups.append(
            {
                "group": name,
                "unique_users": len(values),
                "round_target_observations": int(rows.pairs.sum()),
                "mse": values.mean(axis=0).tolist(),
                "full_gain_vs_shared": float(effect.mean()),
                "descriptive_95_normal_interval": [
                    float(effect.mean() - 1.96 * se),
                    float(effect.mean() + 1.96 * se),
                ]
                if se is not None
                else None,
                "low_support": len(values) < 30,
            }
        )
    np.savez_compressed(
        OUT / "unique-user-evaluation.npz",
        user_keys=unique,
        delta=delta,
        metrics=metric_mean,
        intervals=ci,
    )
    summary = {
        "experiment": "REC045",
        "status": "EXPLORATORY_COMPLETED",
        "independent_confirmation": False,
        "unique_users": len(unique),
        "rounds": round_records,
        "methods_k5_k10": list(SMALL_METHODS),
        "methods_k30": list(METHODS),
        "mse_unique_user_average": metric_mean.mean(axis=0).tolist(),
        "comparisons": comparisons,
        "subgroups_descriptive": groups,
        "inference": {
            "unit": "unique user; input draws averaged within round then rounds within user",
            "bootstrap": 20000,
            "seed": 20260911,
            "family": 13,
            "conditional_on_fixed_training_fits": True,
        },
        "causal_claim": False,
        "service_ranking_measured": False,
        "all_predictions_sealed_before_new_label_read": True,
    }
    write_json(OUT / "summary.json", summary)


def main():
    config = json.loads((DOC / "config.json").read_text(encoding="utf-8"))
    require(
        config.get("metadata_pin_pending") is False, "metadata preparation incomplete"
    )
    review = json.loads((DOC / "execution-review.json").read_text(encoding="utf-8"))
    require(
        review["status"] == "PASS" and review["fingerprint"] == fingerprint(),
        "independent pre-review required",
    )
    for path, expected in config["source_pins"].items():
        require(pin(ROOT / path) == expected, "source drift: " + path)
    require(not OUT.exists(), "preserve earlier run")
    OUT.mkdir(parents=True)
    try:
        frame = load_frame()
        frame.to_parquet(OUT / "metadata.parquet", index=False)
        matrices, info = static_features(frame)
        write_json(OUT / "feature-info.json", info)
        for name, matrix in matrices.items():
            sparse.save_npz(OUT / f"{name}.npz", matrix)
        rounds = [predict_round(frame, matrices, r) for r in range(3)]
        paths = sorted(p for p in OUT.rglob("*") if p.is_file())
        write_json(
            OUT / "prediction-seal.json",
            {
                "status": "PREDICTIONS_SEALED_BEFORE_TARGET_RATINGS",
                "fingerprint": fingerprint(),
                "source_pins": config["source_pins"],
                "rounds": rounds,
                "outputs": {p.relative_to(OUT).as_posix(): pin(p) for p in paths},
            },
        )
        evaluate(frame)
        paths = sorted(p for p in OUT.rglob("*") if p.is_file())
        write_json(
            OUT / "completion-seal.json",
            {
                "status": "COMPLETE",
                "seconds": time.monotonic() - START,
                "fingerprint": fingerprint(),
                "outputs": {p.relative_to(OUT).as_posix(): pin(p) for p in paths},
            },
        )
        print("REC045 COMPLETE; result review required", flush=True)
    except Exception as error:
        write_json(
            OUT / "failure.json", {"type": type(error).__name__, "reason": str(error)}
        )
        raise


if __name__ == "__main__":
    main()
