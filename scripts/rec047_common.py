"""REC047 immutable experiment contract and temporal helpers."""

from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from rec046_common import (
    digest,
    require,
    pin,
    write_json,
    development,
    als_predict,
    pair_accuracy,
)
from rec047_readiness import role

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recommendation/experiments/rec-ev-047"
OUT = ROOT / "outputs/recommendation-evidence/rec-ev-047"
READY = ROOT / "outputs/recommendation-evidence/rec-ev-047-readiness"
ARCHIVE = ROOT.parent / "MM/data/raw/ml-32m.zip"
H = 180 * 86400
KS = [0, 1, 5, 10, 30]
LEVELS = [25, 50, 100]
METHODS = ["ALS", "FM", "ALS_FM", "RIDGE", "GBT"]
FILES = [
    "rec047_common.py",
    "rec047_features.py",
    "rec047_prepare.py",
    "rec047_worker.py",
    "rec047_run.py",
    "rec047_evaluate.py",
    "test_rec047.py",
    "rec047_runtime_smoke.py",
    "rec047_readiness.py",
    "rec046_common.py",
]


def config():
    return json.loads((DOC / "config.json").read_text(encoding="utf-8"))


def fingerprint():
    paths = [ROOT / "scripts" / n for n in FILES] + [
        DOC / "EXECUTION.md",
        DOC / "config.json",
    ]
    return {p.relative_to(ROOT).as_posix(): pin(p) for p in paths}


def reviewed():
    r = json.loads((DOC / "execution-pre-review.json").read_text(encoding="utf-8"))
    require(
        r["status"] == "PASS" and r["fingerprint"] == fingerprint(),
        "reviewed execution version required",
    )
    for p, v in config()["sources"].items():
        require(pin(ROOT / p) == v, "source drift " + p)
    return r


def window_start(stamp, origin):
    stamp = np.asarray(stamp, dtype=np.int64)
    require((stamp < origin).all(), "training timestamp precedes origin")
    return origin - ((origin - 1 - stamp) // H + 1) * H


def assigned_k(uid, start, available):
    choices = [k for k in KS if k <= available]
    return choices[
        int.from_bytes(digest("rec047-k", uid, start)[:8], "big") % len(choices)
    ]


def user_levels(uids):
    train = sorted(
        [int(u) for u in uids if role(int(u)) == "train"],
        key=lambda u: digest("rec047-volume", u),
    )
    result = np.zeros(200949, np.int16)
    for i, u in enumerate(train):
        result[u] = 25 if i < len(train) // 4 else 50 if i < len(train) // 2 else 100
    return result


def stage_seal(stage):
    folder = OUT / stage
    r = json.loads((folder / "prepared-seal.json").read_text(encoding="utf-8"))
    require(r["fingerprint"] == fingerprint(), "prepared version")
    if stage == "evaluation":
        require(
            r["selection"] == pin(OUT / "selection.json"), "prepared selection parent"
        )
    for name, v in r["files"].items():
        require(pin(folder / name) == v, "prepared output drift " + name)
    info = json.loads((folder / "feature-info.json").read_text())
    contexts = json.loads((folder / "contexts.json").read_text())
    ids = np.load(folder / "catalog.npz", allow_pickle=False)["movie_ids"]
    n = check_contexts(contexts, stage, ids, config()["origins"][stage])
    require(n == info["score_rows"], "context score row count")
    return r


def check_prediction(values, n, direct=None):
    values = np.asarray(values)
    require(
        values.shape == (n,) and np.isfinite(values).all(),
        "finite aligned prediction vector",
    )
    if direct is not None:
        require(
            direct.shape == (n,) and direct.dtype == np.dtype(bool),
            "direct coverage vector",
        )


def check_contexts(contexts, stage, ids, origin):
    offset = 0
    keys = set()
    for c in contexts:
        key = (c["uid"], c["k"])
        require(key not in keys and c["k"] in KS, "unique context uid/K")
        keys.add(key)
        require(development(c["uid"]) and role(c["uid"]) == stage, "context role")
        oi, ei = np.asarray(c["oi"], int), np.asarray(c["ei"], int)
        ot, et = (
            np.asarray(c["input_timestamps"], np.int64),
            np.asarray(c["target_timestamps"], np.int64),
        )
        require(
            c["origin"] == origin
            and c["start"] == offset
            and c["stop"] == offset + len(ei)
            and len(ei) > 0,
            "contiguous contexts",
        )
        require(
            len(oi) == len(c["stars"]) == len(ot) == c["k"] <= c["pre_catalog"],
            "context input alignment",
        )
        require(
            len(et) == len(ei)
            and (ot < origin).all()
            and ((et >= origin) & (et < origin + H)).all(),
            "context temporal boundary",
        )
        require(not np.intersect1d(oi, ei).size, "context input/target separation")
        for axis in [oi, ei]:
            require(
                len(np.unique(axis)) == len(axis)
                and ((axis >= 0) & (axis < len(ids))).all(),
                "context unique movie axis",
            )
        require(np.isin(c["stars"], np.arange(1, 11) / 2).all(), "context half stars")
        offset = c["stop"]
    require(offset > 0, "nonempty scoring set")
    return offset


def activity(n):
    return ["0", "1_9", "10_29", "30_99", "100_299", "300_PLUS"][
        np.searchsorted([1, 10, 30, 100, 300], n, side="right")
    ]


def support_group(n):
    return np.asarray(["0", "1_9", "10_49", "50_PLUS"])[
        np.searchsorted([1, 10, 50], n, side="right")
    ]


__all__ = [
    "ROOT",
    "DOC",
    "OUT",
    "READY",
    "ARCHIVE",
    "H",
    "KS",
    "LEVELS",
    "METHODS",
    "config",
    "fingerprint",
    "reviewed",
    "window_start",
    "assigned_k",
    "user_levels",
    "stage_seal",
    "check_prediction",
    "check_contexts",
    "activity",
    "support_group",
    "require",
    "pin",
    "write_json",
    "development",
    "als_predict",
    "pair_accuracy",
    "role",
]
