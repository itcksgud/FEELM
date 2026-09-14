"""Fixed 2019 cutoff; current static metadata conditional temporal diagnostic."""

# ruff: noqa: E402 -- set BLAS threads before NumPy is imported.
from __future__ import annotations

import os

for _thread in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_thread] = "1"

import hashlib
import json
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

from feelm_joint_preferences import (
    METHODS,
    intervals,
    predict_user,
    shared_fit,
    training_statistics,
    unique_user_means,
)
from feelm_preference_structure import pin, require, write_json
from rec_ev_045_joint_preferences import guard

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recommendation/experiments/rec-ev-045"
MAIN = ROOT / "outputs/recommendation-evidence/rec-ev-045"
OUT = ROOT / "outputs/recommendation-evidence/rec-ev-045-temporal"
OLD = ROOT / "outputs/recommendation-evidence/rec-ev-032/user-resplits"
ARCHIVE = Path("C:/higher/projects/MM/data/raw/ml-32m.zip")
CUTOFF = 1546300800
EXPECTED = [
    "e2b19a20f7a7abc5338c74491f2ce8b38d82c6895fe0bc132da94e995e29be07",
    "ed81c5fc1d3f9a5e9129e923cf62304aaba109d5efc7728005f2254ee822d660",
    "2002bf4f477ea10b5a7b5fa8db157c15fba51d7d0c49a50290bf152fda2abfa8",
]


def fingerprint():
    paths = [DOC / "temporal-design.md", DOC / "README.md", DOC / "config.json"]
    paths += [
        ROOT / "scripts" / name
        for name in (
            "rec_ev_045_temporal.py",
            "feelm_joint_preferences.py",
            "rec_ev_045_joint_preferences.py",
            "test_feelm_joint_preferences.py",
            "feelm_residual_taste.py",
            "feelm_preference_structure.py",
        )
    ]
    return {p.relative_to(ROOT).as_posix(): pin(p) for p in paths}


def filtered_fields(raw, allowed, catalog):
    userpart, rest = raw.split(b",", 1)
    uid = int(userpart)
    if uid not in allowed:
        return None
    moviepart, starbytes, timepart = rest.split(b",")
    mid = int(moviepart)
    if mid not in catalog:
        return None
    return uid, mid, int(timepart), starbytes


def raw_rows(allowed, catalog):
    with (
        zipfile.ZipFile(ARCHIVE) as archive,
        archive.open("ml-32m/ratings.csv") as stream,
    ):
        require(
            stream.readline().strip() == b"userId,movieId,rating,timestamp",
            "CSV header",
        )
        for raw in stream:
            row = filtered_fields(raw, allowed, catalog)
            if row is not None:
                yield row


def prepare(frame, splits):
    ids = frame.movie_id.to_numpy(dtype=np.int64)
    catalog = set(map(int, ids))
    index = {int(mid): i for i, mid in enumerate(ids)}
    eval_union = set(map(int, splits["evaluation_user_ids"].ravel()))
    histories = {uid: [[], []] for uid in eval_union}
    for uid, mid, stamp, _ in raw_rows(eval_union, catalog):
        histories[uid][int(stamp >= CUTOFF)].append((stamp, mid))
    eligible = {
        uid
        for uid, (before, after) in histories.items()
        if len(before) >= 30 and len(after) >= 2
    }
    chosen = {
        uid: sorted(histories[uid][0], key=lambda value: (-value[0], value[1]))[:30]
        for uid in eligible
    }
    chosen_set = {(uid, mid) for uid, values in chosen.items() for _, mid in values}
    require(len(eligible) == 300, "metadata-only readiness drift")
    records = []
    for r in range(3):
        people = sorted(
            eligible.intersection(map(int, splits["evaluation_user_ids"][r]))
        )
        digest = hashlib.sha256("\n".join(map(str, people)).encode()).hexdigest()
        require(
            digest == EXPECTED[r] and len(people) >= 100, "fixed readiness cohort drift"
        )
        records.append(
            {
                "round": r,
                "eligible_users": len(people),
                "eligible_id_sha256": digest,
                "post_pairs": sum(len(histories[u][1]) for u in people),
            }
        )
    write_json(
        OUT / "readiness.json",
        {
            "rounds": records,
            "unique_users": len(eligible),
            "cutoff": CUTOFF,
            "E_stars_decoded": 0,
            "current_metadata_conditional": True,
        },
    )
    # Second scan: decode only pre-cutoff training stars and selected pre-cutoff inputs.
    train_union = set(map(int, splits["training_user_ids"].ravel()))
    users, movies, ratings = [], [], []
    o_ratings = {}
    for uid, mid, stamp, starbytes in raw_rows(train_union | eligible, catalog):
        if stamp >= CUTOFF:
            continue
        if uid in train_union:
            users.append(uid)
            movies.append(mid)
            ratings.append(float(starbytes))
        if (uid, mid) in chosen_set:
            o_ratings[(uid, mid)] = float(starbytes)
    train = pd.DataFrame(
        {
            "user_id": np.array(users, dtype=np.int32),
            "movie_id": np.array(movies, dtype=np.int32),
            "rating": np.array(ratings, dtype=np.float32),
        }
    )
    train.to_parquet(OUT / "precutoff-training-pool.parquet", index=False)
    inputs = []
    for r in range(3):
        keys, people = [], []
        for key, uid in zip(
            splits["evaluation_user_keys"][r],
            splits["evaluation_user_ids"][r],
            strict=True,
        ):
            if int(uid) in eligible:
                keys.append(str(key))
                people.append(int(uid))
        oi, stars, eidx, offsets, elapsed, ostamps, estamp = [], [], [], [0], [], [], []
        for uid in people:
            before = chosen[uid]
            after = sorted(histories[uid][1], key=lambda value: (value[0], value[1]))
            oi.append([index[mid] for _, mid in before])
            stars.append([o_ratings[(uid, mid)] for _, mid in before])
            eidx.extend(index[mid] for _, mid in after)
            ostamps.append([stamp for stamp, _ in before])
            estamp.extend(stamp for stamp, _ in after)
            offsets.append(len(eidx))
            elapsed.append(
                (min(t for t, _ in after) - max(t for t, _ in before)) / 86400
            )
        data = dict(
            movie_ids=ids,
            user_keys=np.array(keys),
            evaluation_ids=np.array(people),
            training_ids=splits["training_user_ids"][r],
            o_index=np.array(oi),
            o_ratings=np.array(stars),
            e_index=np.array(eidx),
            e_offsets=np.array(offsets),
            gap_days=np.array(elapsed),
            o_timestamps=np.array(ostamps),
            e_timestamps=np.array(estamp),
        )
        require(
            (data["o_timestamps"] < CUTOFF).all()
            and (data["e_timestamps"] >= CUTOFF).all(),
            "strict temporal boundary",
        )
        inputs.append(data)
    return train, inputs


def run():
    started = time.monotonic()
    review = json.loads(
        (DOC / "temporal-execution-review.json").read_text(encoding="utf-8")
    )
    require(
        review["status"] == "PASS" and review["fingerprint"] == fingerprint(),
        "temporal pre-review required",
    )
    require(
        pin(ARCHIVE)
        == {
            "bytes": 238950008,
            "sha256": "e4a68655d7386b8f95f2f2424b2ff975dfdd15ffd59e0d864a14dca43e99d6ee",
        },
        "archive pin",
    )
    seal = json.loads((MAIN / "completion-seal.json").read_text(encoding="utf-8"))
    sources = [
        "metadata.parquet",
        *(
            name + ".npz"
            for name in (
                "GENRE",
                "KEYWORD",
                "COUNTRY_LANGUAGE",
                "ERA_RUNTIME",
                "DIRECTOR",
                "CAST",
                "COMPANY_COLLECTION",
                "PERSON_ERA",
            )
        ),
    ]
    for name in sources:
        require(
            pin(MAIN / name) == seal["outputs"][name], "metadata/feature source drift"
        )
    config = json.loads((DOC / "config.json").read_text(encoding="utf-8"))
    require(
        pin(OLD / "splits.npz")
        == config["source_pins"][(OLD / "splits.npz").relative_to(ROOT).as_posix()],
        "roles pin",
    )
    require(not OUT.exists(), "preserve prior temporal attempt")
    OUT.mkdir(parents=True)
    try:
        frame = pd.read_parquet(MAIN / "metadata.parquet")
        with np.load(OLD / "splits.npz", allow_pickle=False) as z:
            splits = {k: z[k] for k in z.files}
        for r in range(3):
            require(
                not np.intersect1d(
                    splits["training_user_ids"][r], splits["evaluation_user_ids"][r]
                ).size,
                "full temporal user role overlap",
            )
        train, inputs = prepare(frame, splits)
        static = {name[:-4]: sparse.load_npz(MAIN / name) for name in sources[1:]}
        for r, data in enumerate(inputs):
            folder = OUT / f"r{r}"
            folder.mkdir()
            np.savez_compressed(folder / "input.npz", **data)
            subset = train.loc[train.user_id.isin(data["training_ids"])]
            subset.to_parquet(folder / "training.parquet", index=False)
            stats, crowd = training_statistics(
                subset, data["movie_ids"], data["training_ids"], data["evaluation_ids"]
            )
            matrices = {**static, "CROWD": crowd}
            shared, diagnostics = shared_fit(matrices, stats)
            stats["shared_correction"] = shared
            np.savez_compressed(folder / "training-statistics.npz", **stats)
            sparse.save_npz(folder / "CROWD.npz", crowd)
            write_json(folder / "shared-fit.json", diagnostics)
            prediction = np.empty((len(data["e_index"]), len(METHODS)))
            support = np.empty((len(data["e_index"]), 7), dtype=np.int8)
            for u in range(len(data["user_keys"])):
                rows = slice(data["e_offsets"][u], data["e_offsets"][u + 1])
                prediction[rows], support[rows] = predict_user(
                    matrices,
                    stats["bayes"],
                    shared,
                    data["o_index"][u],
                    data["o_ratings"][u],
                    data["e_index"][rows],
                    True,
                )
                guard()
            np.savez_compressed(
                folder / "predictions.npz",
                predictions=prediction,
                support=support,
                methods=np.array(METHODS),
            )
            print(
                f"temporal r{r}: {len(data['user_keys'])} users predicted, post stars unopened",
                flush=True,
            )
        del train
        files = sorted(p for p in OUT.rglob("*") if p.is_file())
        write_json(
            OUT / "prediction-seal.json",
            {
                "fingerprint": fingerprint(),
                "E_stars_decoded": 0,
                "main_completion": pin(MAIN / "completion-seal.json"),
                "archive": pin(ARCHIVE),
                "outputs": {p.relative_to(OUT).as_posix(): pin(p) for p in files},
            },
        )
        score(inputs)
        files = sorted(p for p in OUT.rglob("*") if p.is_file())
        write_json(
            OUT / "completion-seal.json",
            {
                "status": "COMPLETE",
                "seconds": time.monotonic() - started,
                "fingerprint": fingerprint(),
                "outputs": {p.relative_to(OUT).as_posix(): pin(p) for p in files},
            },
        )
        print("REC045 TEMPORAL COMPLETE; independent audit pending", flush=True)
    except Exception as error:
        write_json(
            OUT / "failure.json", {"type": type(error).__name__, "reason": str(error)}
        )
        raise


def score(inputs):
    seal = json.loads((OUT / "prediction-seal.json").read_text(encoding="utf-8"))
    require(seal["fingerprint"] == fingerprint(), "temporal prediction fingerprint")
    for name, expected in seal["outputs"].items():
        require(pin(OUT / name) == expected, "temporal prediction pin")
    people = set(int(uid) for data in inputs for uid in data["evaluation_ids"])
    catalog = set(map(int, inputs[0]["movie_ids"]))
    # Sole post-cutoff star decoding occurs after every temporal prediction is sealed.
    labels = {
        (uid, mid): float(stars)
        for uid, mid, stamp, stars in raw_rows(people, catalog)
        if stamp >= CUTOFF
    }
    all_keys, deltas, metrics, rounds = [], [], [], []
    for r, data in enumerate(inputs):
        folder = OUT / f"r{r}"
        raw = np.empty(len(data["e_index"]))
        for u, uid in enumerate(data["evaluation_ids"]):
            rows = slice(data["e_offsets"][u], data["e_offsets"][u + 1])
            raw[rows] = [
                labels[(int(uid), int(data["movie_ids"][idx]))]
                for idx in data["e_index"][rows]
            ]
        require(np.isin(raw, np.arange(1, 11) / 2).all(), "temporal E half stars")
        np.savez_compressed(folder / "opened-labels.npz", rating_raw=raw)
        with np.load(folder / "predictions.npz", allow_pickle=False) as z:
            error = z["predictions"] - raw[:, None]
        mse, mae = [], []
        for u in range(len(data["user_keys"])):
            rows = slice(data["e_offsets"][u], data["e_offsets"][u + 1])
            mse.append(np.mean(error[rows] ** 2, axis=0))
            mae.append(np.mean(np.abs(error[rows]), axis=0))
        mse, mae = np.array(mse), np.array(mae)
        delta = np.column_stack(
            [mse[:, 1] - mse[:, 3], mse[:, 2] - mse[:, 3], mse[:, 3] - mse[:, -1]]
        )
        np.savez_compressed(
            folder / "evaluation.npz",
            user_keys=data["user_keys"],
            mse=mse,
            mae=mae,
            delta=delta,
        )
        all_keys.extend(data["user_keys"])
        deltas.append(delta)
        metrics.append(mse)
        rounds.append(
            {
                "round": r,
                "users": len(mse),
                "pairs": len(raw),
                "mse": mse.mean(axis=0).tolist(),
                "mae": mae.mean(axis=0).tolist(),
                "gains": delta.mean(axis=0).tolist(),
                "gap_days_quantiles": np.quantile(
                    data["gap_days"], [0, 0.25, 0.5, 0.75, 1]
                ).tolist(),
            }
        )
    keys, delta = unique_user_means(np.array(all_keys), np.vstack(deltas))
    _, metric = unique_user_means(np.array(all_keys), np.vstack(metrics))
    ci = intervals(delta, family=3, seed=20260912)
    np.savez_compressed(
        OUT / "unique-user-evaluation.npz",
        user_keys=keys,
        delta=delta,
        mse=metric,
        intervals=ci,
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
        }
        for j, name in enumerate(("SHARED_vs_FULL", "GENRE_vs_FULL", "ADD_PERSON_ERA"))
    ]
    write_json(
        OUT / "summary.json",
        {
            "experiment": "REC045-TIME",
            "unique_users": len(keys),
            "rounds": rounds,
            "methods": list(METHODS),
            "mse": metric.mean(axis=0).tolist(),
            "comparisons": comparisons,
            "bootstrap": {"repeats": 20000, "family": 3, "seed": 20260912},
            "current_static_metadata_conditional": True,
            "historical_snapshot_backtest": False,
            "timestamp_is_rating_entry_not_viewing": True,
            "independent_confirmation": False,
            "eligibility_selection": "long-lived observed users ~8 percent of original evaluation cohort",
        },
    )


if __name__ == "__main__":
    run()
