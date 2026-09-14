"""Label-free calendar/user-role feasibility scan before fitting REC047."""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time
import zipfile

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from rec046_common import development, pin

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recommendation/experiments/rec-ev-047"
OUT = ROOT / "outputs/recommendation-evidence/rec-ev-047-readiness"
ARCHIVE = ROOT.parent / "MM/data/raw/ml-32m.zip"
SPLIT = ROOT / "outputs/recommendation-evidence/rec-ev-032/user-resplits/splits.npz"
CATALOG = ROOT / "outputs/recommendation-evidence/rec-ev-046/catalog.npz"
HORIZON = 180 * 86400
KS = [0, 1, 5, 10, 30]
ACTIVITY = ["0", "1_9", "10_29", "30_99", "100_299", "300_PLUS"]


def role(uid):
    n = (
        int.from_bytes(
            hashlib.sha256(f"rec047-role|{uid}".encode()).digest()[:8], "big"
        )
        % 100
    )
    return "train" if n < 70 else "validation" if n < 85 else "evaluation"


def run():
    review = json.loads((DOC / "readiness-pre-review.json").read_text(encoding="utf-8"))
    assert review["status"] == "PASS"
    for f, v in review["fingerprint"].items():
        assert pin(ROOT / f) == v, f
    assert not OUT.exists(), "Preserve existing readiness output"
    OUT.mkdir(parents=True)
    start = time.monotonic()
    uids = np.load(SPLIT)["development_user_ids"]
    ids = np.load(CATALOG)["movie_ids"]
    assert len(uids) == 58014 and all(development(int(u)) for u in uids)
    assert np.all(np.diff(ids) > 0)
    user_lookup = np.zeros(200949, bool)
    user_lookup[uids] = True
    candidates = [
        int(pd.Timestamp(f"{year}-01-01", tz="UTC").timestamp())
        for year in [2023, 2022, 2021]
    ]
    origins = sorted(set(candidates + [v - HORIZON for v in candidates]))
    counts = {
        t: {
            k: np.zeros(200949, np.int64)
            for k in ["pre_all", "pre_catalog", "recent_all", "targets"]
        }
        for t in origins
    }
    target_movies = {
        t: {r: set() for r in ["train", "validation", "evaluation"]} for t in origins
    }
    user_role = np.full(200949, "", dtype="<U10")
    for u in uids:
        user_role[u] = role(int(u))
    schema = pa.schema(
        [("uid", pa.int64()), ("movie_id", pa.int64()), ("timestamp", pa.int64())]
    )
    writer = pq.ParquetWriter(
        OUT / "development-observations.parquet", schema, compression="zstd"
    )
    nraw = 0
    ndevelopment = 0
    ncat = 0
    raw_max_timestamp = 0
    with zipfile.ZipFile(ARCHIVE) as z, z.open("ml-32m/ratings.csv") as f:
        for df in pd.read_csv(
            f,
            usecols=["userId", "movieId", "timestamp"],
            chunksize=1_000_000,
            dtype={"userId": "int64", "movieId": "int64", "timestamp": "int64"},
        ):
            nraw += len(df)
            raw_max_timestamp = max(raw_max_timestamp, int(df.timestamp.max()))
            assert df.userId.min() >= 1 and df.userId.max() <= 200948
            df = df[user_lookup[df.userId.to_numpy()]]
            u = df.userId.to_numpy()
            m = df.movieId.to_numpy()
            s = df.timestamp.to_numpy()
            ndevelopment += len(df)
            ix = np.searchsorted(ids, m)
            cat = (ix < len(ids)) & (ids[np.minimum(ix, len(ids) - 1)] == m)
            ncat += int(cat.sum())
            writer.write_table(
                pa.Table.from_pydict(
                    {"uid": u[cat], "movie_id": m[cat], "timestamp": s[cat]},
                    schema=schema,
                )
            )
            for t, c in counts.items():
                pre = s < t
                target = (s >= t) & (s < t + HORIZON) & cat
                for name, mask in [
                    ("pre_all", pre),
                    ("pre_catalog", pre & cat),
                    ("recent_all", pre & (s >= t - 365 * 86400)),
                    ("targets", target),
                ]:
                    c[name] += np.bincount(u[mask], minlength=200949)
                for r in ["train", "validation", "evaluation"]:
                    target_movies[t][r].update(
                        map(int, m[target & (user_role[u] == r)])
                    )
    writer.close()
    assert nraw == 32000204
    assert raw_max_timestamp >= max(candidates) + HORIZON
    user_rows = []
    summary = []
    for t, c in counts.items():
        a = np.asarray(ACTIVITY)[
            np.searchsorted([1, 10, 30, 100, 300], c["pre_all"][uids], side="right")
        ]
        table = pd.DataFrame(
            {
                "origin": t,
                "uid": uids,
                "role": user_role[uids],
                "activity": a,
                **{key: v[uids] for key, v in c.items()},
            }
        )
        user_rows.append(table)
        for r in ["train", "validation", "evaluation"]:
            for b in ["ALL", *ACTIVITY]:
                pool = table[
                    (table.role == r) & ((table.activity == b) if b != "ALL" else True)
                ]
                for k in KS:
                    x = pool[(pool.pre_catalog >= k) & (pool.targets >= 1)]
                    summary.append(
                        {
                            "origin": t,
                            "role": r,
                            "activity": b,
                            "k": k,
                            "pool_users": len(pool),
                            "input_eligible_users": int((pool.pre_catalog >= k).sum()),
                            "target_users": len(x),
                            "target2plus_users": int((x.targets >= 2).sum()),
                            "target_rows": int(x.targets.sum()),
                            "mean_pre_all": float(x.pre_all.mean()) if len(x) else None,
                            "mean_recent_all": float(x.recent_all.mean())
                            if len(x)
                            else None,
                        }
                    )
    pd.concat(user_rows, ignore_index=True).to_parquet(
        OUT / "user-origin-counts.parquet", index=False
    )
    pd.DataFrame(summary).to_csv(OUT / "availability.csv", index=False)
    available = pd.DataFrame(summary)
    selected = None
    for t in candidates:
        checks = []
        for r, o in [("validation", t - HORIZON), ("evaluation", t)]:
            for k in [0, 1]:
                a = available[
                    (available.origin == o)
                    & (available.role == r)
                    & (available.activity == "ALL")
                    & (available.k == k)
                ]
                assert len(a) == 1
                checks.append(int(a.target_users.iloc[0]) >= 100)
        if all(checks):
            selected = t
            break
    result = {
        "status": "CALCULATED_PENDING_REVIEW",
        "selected_evaluation_origin": selected,
        "selected_validation_origin": selected - HORIZON if selected else None,
        "horizon_seconds": HORIZON,
        "raw_rows": nraw,
        "raw_max_timestamp": raw_max_timestamp,
        "development_rows": ndevelopment,
        "catalog_development_rows": ncat,
        "raw_rating_values_loaded": 0,
        "roles": {
            r: int((user_role[uids] == r).sum())
            for r in ["train", "validation", "evaluation"]
        },
        "target_unique_movies_k0_activity_all": {
            str(t): {r: len(v) for r, v in rs.items()}
            for t, rs in target_movies.items()
        },
        "seconds": time.monotonic() - start,
        "fingerprint": review["fingerprint"],
        "files": {p.name: pin(p) for p in OUT.iterdir() if p.is_file()},
    }
    for f, v in review["fingerprint"].items():
        assert pin(ROOT / f) == v, f
    (OUT / "completion.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k
                not in ["fingerprint", "files", "target_unique_movies_k0_activity_all"]
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    run()
