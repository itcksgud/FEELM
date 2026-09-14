"""Prepare only reviewed, allowed training/input ratings; target stars stay closed."""

# ruff: noqa: E402 -- fix BLAS threads before importing NumPy.
from __future__ import annotations
import os

for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[name] = "1"
import gc
import json
import time
import zipfile
from collections import defaultdict

import numpy as np
import pandas as pd
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import sparse

from rec046_common import (
    ROOT,
    DOC,
    OUT,
    require,
    write_json,
    pin,
    digest,
    development,
    withheld,
    reviewed,
    fingerprint,
    movie_tokens,
    fit_features,
    pair_features,
)

ARCHIVE = ROOT.parent / "MM/data/raw/ml-32m.zip"


def raw_rows(allowed, catalog):
    with (
        zipfile.ZipFile(ARCHIVE) as archive,
        archive.open("ml-32m/ratings.csv") as stream,
    ):
        require(
            stream.readline().strip() == b"userId,movieId,rating,timestamp",
            "archive header",
        )
        for raw in stream:
            upart, rest = raw.split(b",", 1)
            uid = int(upart)
            if uid not in allowed:
                continue
            mpart, rating, stamp = rest.strip().split(b",")
            mid = int(mpart)
            if mid not in catalog:
                continue
            yield uid, mid, int(stamp), rating


def guard():
    require(psutil.Process().memory_info().rss < 6 * 1024**3, "host prepare 6GiB cap")


class FeatureWriter:
    def __init__(self, path):
        self.schema = pa.schema(
            [
                ("row_id", pa.int64()),
                ("label", pa.float64()),
                ("indices", pa.list_(pa.int32())),
                ("values", pa.list_(pa.float32())),
            ]
        )
        self.writer = pq.ParquetWriter(path, self.schema, compression="zstd")
        self.offset = 0
        self.blocks = []
        self.labels = []
        self.pending = 0

    def append(self, x, y):
        self.blocks.append(x)
        self.labels.extend(map(float, y))
        self.pending += x.shape[0]
        if self.pending >= 4096:
            self.flush()

    def flush(self):
        if not self.pending:
            return
        x = sparse.vstack(self.blocks, format="csr")
        n = x.shape[0]
        table = pa.Table.from_pydict(
            {
                "row_id": np.arange(self.offset, self.offset + n),
                "label": self.labels,
                "indices": [x.indices[x.indptr[i] : x.indptr[i + 1]] for i in range(n)],
                "values": [x.data[x.indptr[i] : x.indptr[i + 1]] for i in range(n)],
            },
            schema=self.schema,
        )
        self.writer.write_table(table)
        self.offset += n
        self.blocks = []
        self.labels = []
        self.pending = 0
        guard()

    def close(self):
        self.flush()
        self.writer.close()


def prepare():
    reviewed()
    cfg = json.loads((DOC / "config.json").read_text(encoding="utf-8"))
    require(not OUT.exists(), "preserve existing REC046 outputs")
    for path, expected in cfg["sources"].items():
        require(pin(ROOT / path) == expected, "source drift " + path)
    OUT.mkdir(parents=True)
    start = time.monotonic()
    frame = pd.read_parquet(
        ROOT / "outputs/recommendation-evidence/rec-ev-045/metadata.parquet"
    )
    ids = frame.movie_id.to_numpy(dtype=int)
    lookup = {int(v): i for i, v in enumerate(ids)}
    require(len(ids) == 85517 and np.array_equal(ids, np.unique(ids)), "catalog order")
    z = np.load(
        ROOT / "outputs/recommendation-evidence/rec-ev-032/user-resplits/splits.npz",
        allow_pickle=False,
    )
    train_sets = [set(map(int, a)) for a in z["training_user_ids"]]
    test_sets = [set(map(int, a)) for a in z["evaluation_user_ids"]]
    eval_pool = set.union(*test_sets)
    allowed = set.union(*train_sets, *test_sets)
    require(all(development(u) for u in allowed), "forbidden role")
    require(
        all(not (a & b) for a, b in zip(train_sets, test_sets)), "original role overlap"
    )
    pre = defaultdict(list)
    post = defaultdict(list)
    precounts = np.zeros(len(ids), dtype=np.int64)
    for uid, mid, stamp, _ in raw_rows(allowed, lookup):
        (pre if stamp < cfg["cutoff"] else post)[uid].append((stamp, mid))
        if stamp < cfg["cutoff"]:
            precounts[lookup[mid]] += 1
    guard()
    eligible = {u for u in eval_pool if len(pre[u]) >= 30 and len(post[u]) >= 2}
    require(len(eligible) == 300, "fixed temporal eval cohort changed")
    val_eligible = {
        u
        for u in set.intersection(*train_sets) - eval_pool
        if len(pre[u]) >= 30 and len(post[u]) >= 2
    }
    val = sorted(val_eligible, key=lambda u: digest("rec046-val", u))[
        : cfg["validation_users"]
    ]
    require(len(val) >= 100, "insufficient temporal validation users")
    valset = set(val)
    inputs = {
        u: sorted(pre[u], key=lambda a: (-a[0], a[1]))[:30] for u in eligible | valset
    }
    held = np.array([withheld(int(v)) for v in ids])
    selections = []
    counts = []
    for r, pool in enumerate(train_sets):
        records = []
        short = 0
        for u in sorted(
            pool - eval_pool - valset, key=lambda u: digest("rec046-train", r, u)
        ):
            k = cfg["ks"][
                int.from_bytes(digest("rec046-k", r, u)[:8], "big") % len(cfg["ks"])
            ]
            available = [a for a in pre[u] if not held[lookup[a[1]]]]
            if len(available) < k + cfg["training_targets"]:
                short += 1
                continue
            rows = sorted(
                sorted(available, key=lambda a: digest("rec046-R", r, u, a[1]))[
                    : k + cfg["training_targets"]
                ]
            )
            records.append({"uid": u, "k": k, "rows": rows})
            if len(records) == cfg["train_users"]:
                break
        require(len(records) == cfg["train_users"], "insufficient training users")
        selections.append(records)
        counts.append(
            {
                "round": r,
                "users": len(records),
                "skipped_insufficient": short,
                "ratings": sum(len(a["rows"]) for a in records),
                "target_rows": len(records) * cfg["training_targets"],
                "k_users": {
                    str(k): sum(a["k"] == k for a in records) for k in cfg["ks"]
                },
            }
        )
    chosen = {
        (a["uid"], mid) for rows in selections for a in rows for _, mid in a["rows"]
    }
    chosen.update((u, mid) for u, rows in inputs.items() for _, mid in rows)
    selected_users = {u for u, _ in chosen}
    stars = {}
    for u, mid, stamp, rb in raw_rows(selected_users, lookup):
        if stamp < cfg["cutoff"] and (u, mid) in chosen:
            value = float(rb)
            require(value in np.arange(1, 11) / 2, "raw half stars")
            stars[(u, mid)] = value
    require(len(stars) == len(chosen), "missing chosen input/training ratings")
    del pre, chosen
    gc.collect()
    write_json(
        OUT / "roles.json",
        {
            "validation": val,
            "evaluation_union": sorted(eligible),
            "all_evaluation_pool": sorted(eval_pool),
            "selections": selections,
            "prelabel": True,
        },
    )
    write_json(
        OUT / "readiness.json",
        {
            "eval_unique": len(eligible),
            "val_users": len(val),
            "val_eligible": len(val_eligible),
            "rounds": counts,
            "cutoff": cfg["cutoff"],
            "target_ratings_decoded": 0,
        },
    )
    tokens = movie_tokens(frame)
    np.savez_compressed(
        OUT / "catalog.npz",
        movie_ids=ids,
        held=held,
        precounts=precounts,
        years=frame.release_year.fillna(0).to_numpy(dtype=int),
    )
    for r, records in enumerate(selections):
        folder = OUT / f"r{r}"
        folder.mkdir()
        training = [
            (a["uid"], mid, stars[(a["uid"], mid)])
            for a in records
            for _, mid in a["rows"]
        ]
        require(
            not {u for u, _, _ in training} & (eval_pool | valset),
            "global user separation",
        )
        require(all(not withheld(mid) for _, mid, _ in training), "cold training leak")
        pd.DataFrame(training, columns=["user", "item", "rating"]).to_csv(
            folder / "als.csv", header=False, index=False
        )
        mu = float(np.mean([v for _, _, v in training]))
        n = np.bincount([lookup[m] for _, m, _ in training], minlength=len(ids))
        fit_idx = np.flatnonzero(n)
        x, vocab, support = fit_features(tokens, fit_idx, cfg["caps"])
        sparse.save_npz(folder / "movie-features.npz", x)
        write_json(
            folder / "feature-info.json",
            {
                "vocabulary": vocab,
                "catalog_support": support,
                "movie_columns": x.shape[1],
                "pair_columns": 5 * x.shape[1] + 2,
                "global_mean": mu,
                "training_ratings": len(training),
            },
        )
        np.save(folder / "train-counts.npy", n)
        writer = FeatureWriter(folder / "train.parquet")
        for a in records:
            u = a["uid"]
            k = a["k"]
            rows = a["rows"]
            oi = [lookup[mid] for _, mid in rows[:k]]
            ei = [lookup[mid] for _, mid in rows[k:]]
            ov = [stars[(u, mid)] for _, mid in rows[:k]]
            y = [stars[(u, mid)] for _, mid in rows[k:]]
            require(len(y) == cfg["training_targets"], "same target count")
            writer.append(pair_features(x, oi, ov, ei, mu), y)
        writer.close()
        writer = FeatureWriter(folder / "score.parquet")
        contexts = []
        offset = 0
        for role, users in [
            ("validation", val),
            ("evaluation", sorted(test_sets[r] & eligible)),
        ]:
            for u in users:
                es = sorted(post[u])
                ei = [lookup[mid] for _, mid in es]
                before = inputs[u]
                require(
                    max(t for t, _ in before) < cfg["cutoff"] <= min(t for t, _ in es),
                    "time leak",
                )
                for k in cfg["ks"]:
                    oi = [lookup[mid] for _, mid in before[:k]]
                    ov = [stars[(u, mid)] for _, mid in before[:k]]
                    writer.append(pair_features(x, oi, ov, ei, mu), np.zeros(len(ei)))
                    contexts.append(
                        {
                            "uid": u,
                            "role": role,
                            "k": k,
                            "start": offset,
                            "stop": offset + len(ei),
                            "oi": oi,
                            "stars": ov,
                            "ei": ei,
                            "input_supported": int(np.count_nonzero(n[oi])),
                            "input_withheld": int(np.count_nonzero(held[oi])),
                        }
                    )
                    offset += len(ei)
        writer.close()
        write_json(folder / "contexts.json", contexts)
        require(writer.offset == offset, "score row alignment")
        print(
            f"prepared r{r}: {len(records)} users, {len(training)} ALS ratings, {offset} score rows; target stars closed",
            flush=True,
        )
        del x, training
        gc.collect()
        guard()
    write_json(
        OUT / "prepared-seal.json",
        {
            "fingerprint": fingerprint(),
            "seconds": time.monotonic() - start,
            "files": {
                p.relative_to(OUT).as_posix(): pin(p)
                for p in OUT.rglob("*")
                if p.is_file()
            },
            "target_stars_decoded": 0,
        },
    )


if __name__ == "__main__":
    prepare()
