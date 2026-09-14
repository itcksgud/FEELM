"""REC046 shared pure transforms and guards. No implicit data reads."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/recommendation/experiments/rec-ev-046"
OUT = ROOT / "outputs/recommendation-evidence/rec-ev-046"
BLOCKS = (
    "GENRE",
    "KEYWORD",
    "COUNTRY_LANGUAGE",
    "ERA_RUNTIME",
    "DIRECTOR",
    "CAST",
    "COMPANY_COLLECTION",
)


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def pin(path):
    path = Path(path)
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return {"bytes": path.stat().st_size, "sha256": h.hexdigest()}


def digest(*values):
    return hashlib.sha256("|".join(map(str, values)).encode()).digest()


def bucket(uid, prefix, modulus, first8=False):
    value = hashlib.sha256(f"{prefix}{uid}".encode()).digest()
    return int.from_bytes(value[:8] if first8 else value, "big") % modulus


def development(uid):
    return (
        0 < uid <= 200948
        and bucket(uid, "feelm-rec-vnext-user-split-v1|", 100, True) <= 59
        and bucket(uid, "rec-ev-022a-user-role-v1|", 10000) <= 5999
        and bucket(uid, "rec-ev-028-user-phase-v1|", 10000) <= 7999
    )


def withheld(mid):
    return int.from_bytes(digest("rec046-movie", mid)[:8], "big") % 5 == 0


def fingerprint():
    paths = [DOC / "README.md", DOC / "config.json"]
    paths += [
        ROOT / "scripts" / name
        for name in (
            "rec046_common.py",
            "rec046_prepare.py",
            "rec046_worker.py",
            "rec046_run.py",
            "rec046_evaluate.py",
            "test_rec046.py",
        )
    ]
    paths += [ROOT / "performance/rec046.Dockerfile"]
    return {p.relative_to(ROOT).as_posix(): pin(p) for p in paths}


def reviewed():
    review = json.loads((DOC / "pre-review.json").read_text(encoding="utf-8"))
    require(
        review["status"] == "PASS" and review["fingerprint"] == fingerprint(),
        "reviewed version required",
    )


def verify_prepared():
    cfg = json.loads((DOC / "config.json").read_text(encoding="utf-8"))
    for source, expected in cfg["sources"].items():
        require(pin(ROOT / source) == expected, "source drift: " + source)
    prepared = json.loads((OUT / "prepared-seal.json").read_text(encoding="utf-8"))
    require(prepared["fingerprint"] == fingerprint(), "prepared version mismatch")
    for name, expected in prepared["files"].items():
        require(pin(OUT / name) == expected, "prepared file drift: " + name)
    return prepared


def movie_tokens(frame):
    rows = []

    def ts(values, prefix):
        return {prefix + str(v) for v in values}

    for r in frame.itertuples(index=False):
        era = str(int(r.release_year) // 10 * 10) if pd.notna(r.release_year) else ""
        runtime = (
            str(int(r.runtime_minutes) // 30 * 30)
            if pd.notna(r.runtime_minutes) and r.runtime_minutes > 0
            else ""
        )
        rows.append(
            [
                ts([v for v in r.genre_ids if v != 10770], "g:"),
                ts(r.keyword_ids, "k:"),
                ts(r.production_country_codes, "c:")
                | ({"l:" + r.original_language} if r.original_language else set()),
                ({"e:" + era} if era else set())
                | ({"r:" + runtime} if runtime else set()),
                ts(r.director_ids, "d:"),
                ts(r.top5_cast_ids, "a:"),
                ts(r.production_company_ids, "p:") | ts(r.collection_ids, "s:"),
            ]
        )
    return rows


def fit_features(tokens, fit_indices, caps):
    matrices = []
    vocabulary = {}
    supports = {}
    for b, name in enumerate(BLOCKS):
        frequency = Counter(v for i in sorted(set(fit_indices)) for v in tokens[i][b])
        values = sorted(frequency, key=lambda v: (-frequency[v], v))[: caps[name]]
        lookup = {v: i for i, v in enumerate(values)}
        oov = len(values)
        ri = []
        ci = []
        data = []
        hit = unknown = 0
        for i, row in enumerate(tokens):
            mapped = sorted({lookup.get(v, oov) for v in row[b]})
            hit += sum(v in lookup for v in row[b])
            unknown += sum(v not in lookup for v in row[b])
            ri.extend([i] * len(mapped))
            ci.extend(mapped)
            data.extend([1 / np.sqrt(len(mapped))] * len(mapped))
        matrices.append(
            sparse.csr_matrix(
                (data, (ri, ci)), shape=(len(tokens), oov + 1), dtype=np.float32
            )
        )
        vocabulary[name] = values
        supports[name] = {
            "columns": oov + 1,
            "known_catalog_tokens": hit,
            "oov_catalog_tokens": unknown,
        }
    return sparse.hstack(matrices, format="csr"), vocabulary, supports


def pair_features(x, oi, stars, ei, global_mean):
    oi = np.asarray(oi, dtype=np.int64)
    ei = np.asarray(ei, dtype=np.int64)
    stars = np.asarray(stars, dtype=float)
    require(len(oi) == len(stars) and not np.intersect1d(oi, ei).size, "O/E separation")
    require(np.isin(stars, np.arange(1, 11) / 2).all(), "half star inputs")
    d = x.shape[1]
    n = len(ei)
    if len(oi):
        p = sparse.csr_matrix(x[oi].sum(axis=0) / len(oi))
        q = sparse.csr_matrix(
            (x[oi].multiply((stars / 5)[:, None])).sum(axis=0) / len(oi)
        )
        mean = float(stars.mean())
    else:
        p = q = sparse.csr_matrix((1, d), dtype=np.float32)
        mean = global_mean
    p = sparse.vstack([p] * n, format="csr")
    q = sparse.vstack([q] * n, format="csr")
    candidate = x[ei]
    result = sparse.hstack(
        [
            candidate,
            p,
            q,
            candidate.multiply(p),
            candidate.multiply(q),
            sparse.csr_matrix(np.tile([mean / 5, len(oi) / 30], (n, 1))),
        ],
        format="csr",
        dtype=np.float32,
    )
    result.sort_indices()
    require(np.isfinite(result.data).all(), "finite features")
    return result


def als_predict(factors, movie_ids, oi, stars, ei, reg, mu):
    supported = np.isfinite(factors).all(axis=1)
    oi = np.asarray(oi, dtype=int)
    ei = np.asarray(ei, dtype=int)
    stars = np.asarray(stars, dtype=float)
    valid = supported[oi]
    fallback = (stars.sum() + 5 * mu) / (len(stars) + 5)
    direct = supported[ei] & bool(valid.any())
    result = np.full(len(ei), fallback)
    if valid.any():
        y = factors[oi[valid]].astype(float)
        u = np.linalg.solve(
            y.T @ y + reg * valid.sum() * np.eye(y.shape[1]), y.T @ stars[valid]
        )
        result[direct] = factors[ei[direct]] @ u
    require(np.isfinite(result).all(), "finite ALS predictions")
    return result, direct, int(valid.sum())


def pair_accuracy(y, p):
    """Exact unequal-rating pairs, O(n log n), including prediction ties."""
    y = np.asarray(y)
    p = np.asarray(p)
    correct = 0.0
    total = 0
    for high in np.unique(y):
        low = np.sort(p[y < high])
        upper = p[y == high]
        if not len(low):
            continue
        left = np.searchsorted(low, upper, "left")
        right = np.searchsorted(low, upper, "right")
        correct += float(np.sum((left + right) / 2))
        total += len(low) * len(upper)
    return correct / total if total else None


def fm_manual(x, intercept, linear, factors):
    xv = x @ factors
    return np.asarray(
        intercept
        + x @ linear
        + 0.5 * np.sum(xv * xv - x.multiply(x) @ (factors * factors), axis=1)
    ).ravel()
