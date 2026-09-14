"""Complete allowed history, chronological episodes, closed target labels."""

from __future__ import annotations
import argparse
import gc
import json
import time
import zipfile
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import psutil
from rec047_common import (
    ROOT,
    OUT,
    READY,
    ARCHIVE,
    H,
    KS,
    LEVELS,
    config,
    reviewed,
    fingerprint,
    user_levels,
    window_start,
    assigned_k,
    require,
    pin,
    write_json,
)
from rec047_features import Relations


def raw_rows(allowed, catalog):
    with zipfile.ZipFile(ARCHIVE) as z, z.open("ml-32m/ratings.csv") as f:
        require(
            f.readline().strip() == b"userId,movieId,rating,timestamp", "raw header"
        )
        for row in f:
            u, rest = row.split(b",", 1)
            uid = int(u)
            if uid not in allowed:
                continue
            m, r, t = rest.strip().split(b",")
            mid = int(m)
            if mid in catalog:
                yield uid, mid, int(t), r


def guard():
    require(
        psutil.Process().memory_info().rss < 6 * 1024**3, "host preparation 6GiB cap"
    )


class Writer:
    def __init__(self, path, dim):
        self.cols = [f"x{i:03d}" for i in range(dim)]
        self.schema = pa.schema(
            [
                ("row_id", pa.int64()),
                ("uid", pa.int32()),
                ("level", pa.int16()),
                ("label", pa.float64()),
            ]
            + [(c, pa.float32()) for c in self.cols]
        )
        self.writer = pq.ParquetWriter(path, self.schema, compression="zstd")
        self.pending = []
        self.offset = 0
        self.size = 0

    def append(self, x, y, uid, level):
        dense = x.toarray()
        n = len(y)
        require(len(dense) == n, "feature label alignment")
        self.pending.append((dense, np.asarray(y), uid, level))
        self.size += n
        if self.size >= 8192:
            self.flush()

    def flush(self):
        if not self.size:
            return
        x = np.vstack([v[0] for v in self.pending])
        y = np.concatenate([v[1] for v in self.pending])
        data = {
            "row_id": np.arange(self.offset, self.offset + len(y)),
            "uid": np.concatenate([np.full(len(v[1]), v[2]) for v in self.pending]),
            "level": np.concatenate([np.full(len(v[1]), v[3]) for v in self.pending]),
            "label": y,
        }
        data.update({c: x[:, i] for i, c in enumerate(self.cols)})
        self.writer.write_table(pa.Table.from_pydict(data, schema=self.schema))
        self.offset += len(y)
        self.pending = []
        self.size = 0
        guard()

    def close(self):
        self.flush()
        self.writer.close()


def prepare(stage):
    reviewed()
    cfg = config()
    folder = OUT / stage
    require(not folder.exists(), "preserve existing stage")
    if stage == "evaluation":
        selection = json.loads((OUT / "selection.json").read_text(encoding="utf-8"))
        require(
            selection["fingerprint"] == fingerprint(), "validation selection version"
        )
        require(
            selection["validation_fit_seal"] == pin(OUT / "validation/fit-seal.json"),
            "validation fit seal",
        )
        require(
            selection["validation_labels"] == pin(OUT / "validation/labels.parquet"),
            "validation label seal",
        )
    folder.mkdir(parents=True)
    begin = time.monotonic()
    origin = cfg["origins"][stage]
    metadata = pd.read_parquet(ROOT / cfg["metadata"])
    ids = metadata.movie_id.to_numpy(dtype=np.int64)
    require(np.array_equal(ids, np.unique(ids)) and len(ids) == 85517, "catalog order")
    lookup = {int(m): i for i, m in enumerate(ids)}
    dev = np.load(ROOT / cfg["split"], allow_pickle=False)["development_user_ids"]
    levels = user_levels(dev)
    train_users = set(map(int, np.flatnonzero(levels)))
    counts = pd.read_parquet(READY / "user-origin-counts.parquet")
    eligible = counts[
        (counts.origin == origin) & (counts.role == stage) & (counts.targets >= 1)
    ]
    scored_users = set(map(int, eligible.uid))
    require(not train_users & scored_users, "user separation")
    observations = pd.read_parquet(READY / "development-observations.parquet")
    score_obs = observations[observations.uid.isin(scored_users)]
    before = (
        score_obs[score_obs.timestamp < origin]
        .sort_values(["uid", "timestamp", "movie_id"], ascending=[True, False, True])
        .groupby("uid", sort=False)
        .head(30)
    )
    inputs = {
        (int(u), int(m))
        for u, m in before[["uid", "movie_id"]].itertuples(index=False, name=None)
    }
    target = score_obs[
        (score_obs.timestamp >= origin) & (score_obs.timestamp < origin + H)
    ].sort_values(["uid", "timestamp", "movie_id"])
    allowed = train_users | scored_users
    del observations, score_obs
    gc.collect()
    schema = pa.schema(
        [
            ("uid", pa.int32()),
            ("movie_id", pa.int32()),
            ("timestamp", pa.int64()),
            ("rating", pa.float64()),
            ("level", pa.int16()),
        ]
    )
    writer = pq.ParquetWriter(folder / "ratings.parquet", schema, compression="zstd")
    batch = []
    stars = {}
    n = 0
    for u, m, t, rb in raw_rows(allowed, lookup):
        if t >= origin:
            continue
        training = u in train_users
        if not training and (u, m) not in inputs:
            continue
        rating = float(rb)
        require(rating * 2 in range(1, 11), "half star raw rating")
        if training:
            batch.append((u, m, t, rating, int(levels[u])))
            if len(batch) >= 250000:
                writer.write_table(
                    pa.Table.from_pandas(
                        pd.DataFrame(batch, columns=schema.names),
                        schema=schema,
                        preserve_index=False,
                    )
                )
                n += len(batch)
                batch = []
                guard()
        else:
            stars[(u, m)] = rating
    if batch:
        writer.write_table(
            pa.Table.from_pandas(
                pd.DataFrame(batch, columns=schema.names),
                schema=schema,
                preserve_index=False,
            )
        )
        n += len(batch)
    writer.close()
    del batch
    require(len(stars) == len(inputs), "all allowed score inputs decoded")
    train = pd.read_parquet(folder / "ratings.parquet").sort_values(
        ["uid", "timestamp", "movie_id"]
    )
    require(
        len(train) == n and not train.duplicated(["uid", "movie_id"]).any(),
        "unique full histories",
    )
    # No artificial movie removal: natural catalog support is the main target here.
    train["index"] = np.searchsorted(ids, train.movie_id.to_numpy())
    train["window"] = window_start(train.timestamp.to_numpy(), origin)
    stats = {}
    movie_counts = []
    for level in LEVELS:
        rows = train[train.level <= level]
        expected = counts[(counts.origin == origin) & (counts.role == "train")]
        expected = expected[levels[expected.uid.to_numpy()] <= level]
        require(
            len(rows) == int(expected.pre_catalog.sum()),
            "complete label-free training row counts",
        )
        movie_counts.append(np.bincount(rows["index"], minlength=len(ids)))
        stats[str(level)] = {
            "pool_users": int(((levels > 0) & (levels <= level)).sum()),
            "effective_users": int(rows.uid.nunique()),
            "ratings": len(rows),
            "movies": int(rows.movie_id.nunique()),
            "global_mean": float(rows.rating.mean()),
        }
    np.savez_compressed(
        folder / "catalog.npz", movie_ids=ids, counts=np.asarray(movie_counts)
    )
    relations = Relations(metadata)
    require(len(relations.names) == 168, "fixed features")
    fw = Writer(folder / "train.parquet", len(relations.names))
    episodes = []
    total_training_users = train.uid.nunique()
    for number, (uid, a) in enumerate(train.groupby("uid", sort=False)):
        ix = a["index"].to_numpy()
        r = a.rating.to_numpy()
        ts = a.timestamp.to_numpy()
        wins = a.window.to_numpy()
        starts = np.r_[0, np.flatnonzero(np.diff(wins)) + 1]
        ends = np.r_[starts[1:], len(a)]
        for lo, hi in zip(starts, ends):
            w = int(wins[lo])
            k = assigned_k(int(uid), w, int(lo))
            previous = np.lexsort((ids[ix[:lo]], -ts[:lo]))[:k]
            oi = ix[previous]
            ov = r[previous]
            ei = ix[lo:hi]
            require(
                (ts[previous] < w).all()
                and ((ts[lo:hi] >= w) & (ts[lo:hi] < w + H)).all(),
                "episode horizon",
            )
            fw.append(
                relations.transform(oi, ov, ei), r[lo:hi], int(uid), int(levels[uid])
            )
            episodes.append(
                (
                    int(uid),
                    int(levels[uid]),
                    w,
                    k,
                    int(lo),
                    int(hi - lo),
                    int(ts[previous].max()) if k else None,
                )
            )
        if number % 2000 == 0:
            print(f"{stage} features users {number}/{total_training_users}", flush=True)
    fw.close()
    require(fw.offset == len(train), "each training rating exactly one target")
    pd.DataFrame(
        episodes,
        columns=[
            "uid",
            "level",
            "origin",
            "k",
            "pre_catalog",
            "target_rows",
            "latest_input_timestamp",
        ],
    ).to_parquet(folder / "episodes.parquet", index=False)
    del train
    gc.collect()
    fw = Writer(folder / "score.parquet", len(relations.names))
    contexts = []
    bg = {int(u): a for u, a in before.groupby("uid", sort=False)}
    activity_map = eligible.set_index("uid").to_dict("index")
    for uid, a in target.groupby("uid", sort=True):
        uid = int(uid)
        o = bg.get(uid, before.iloc[:0])
        ei = np.searchsorted(ids, a.movie_id.to_numpy())
        for k in KS:
            if len(o) < k:
                continue
            use = o.head(k)
            oi = np.searchsorted(ids, use.movie_id.to_numpy())
            ov = [stars[(uid, int(m))] for m in use.movie_id]
            start = fw.offset + fw.size
            fw.append(relations.transform(oi, ov, ei), np.zeros(len(ei)), uid, 0)
            contexts.append(
                {
                    "uid": uid,
                    "k": k,
                    "origin": origin,
                    "start": start,
                    "stop": start + len(ei),
                    "oi": oi.tolist(),
                    "stars": ov,
                    "ei": ei.tolist(),
                    "input_timestamps": use.timestamp.to_list(),
                    "target_timestamps": a.timestamp.to_list(),
                    "activity": activity_map[uid]["activity"],
                    "pre_all": activity_map[uid]["pre_all"],
                    "pre_catalog": activity_map[uid]["pre_catalog"],
                }
            )
    fw.close()
    require(
        fw.offset == sum(c["stop"] - c["start"] for c in contexts), "score alignment"
    )
    write_json(folder / "contexts.json", contexts)
    write_json(
        folder / "feature-info.json",
        {
            "stage": stage,
            "origin": origin,
            "dimensions": len(relations.names),
            "feature_names": relations.names,
            "identity_blocks": relations.info,
            "levels": stats,
            "score_rows": fw.offset,
            "score_users": len(eligible),
            "decoded_input_ratings": len(stars),
            "target_stars_decoded": 0,
            "forced_movie_holdout": False,
        },
    )
    write_json(
        folder / "prepared-seal.json",
        {
            "fingerprint": fingerprint(),
            "selection": pin(OUT / "selection.json") if stage == "evaluation" else None,
            "seconds": time.monotonic() - begin,
            "files": {p.name: pin(p) for p in folder.iterdir() if p.is_file()},
            "target_stars_decoded": 0,
        },
    )
    print(
        json.dumps(
            {
                "stage": stage,
                "seconds": time.monotonic() - begin,
                "levels": stats,
                "score_rows": fw.offset,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["validation", "evaluation"])
    prepare(parser.parse_args().stage)
