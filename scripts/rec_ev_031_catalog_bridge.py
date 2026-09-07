"""REC-EV-031: audited, exploratory catalogue bridge; no product changes."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import sys
import time
import traceback
import zipfile

import numpy as np
import pandas as pd
from scipy import sparse

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
DEFAULT = ROOT / "docs/recommendation/experiments/rec-ev-031/config.json"
AUDIT = ROOT / "docs/recommendation/experiments/rec-ev-031/implementation-review.json"
KS = (5, 10, 30)
POLICIES = ("BAYES", "COUNT", "CONTENT", "UNION", "SHUFFLE")
PROFILE_COLUMNS = ["cohort", "outer", "user_key", "profile_movie_ids", "target_movie_ids", "profile_rating_indices"]
LABEL_COLUMNS = ["outer", "user_key", "target_movie_ids", "target_q"]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def identity(path):
    p = Path(path)
    return {"bytes": p.stat().st_size, "sha256": sha(p)}


def write_json(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_npz(path, **arrays):
    p = Path(path)
    tmp = p.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, **arrays)
    os.replace(tmp, p)


def write_parquet(path, frame):
    p = Path(path)
    tmp = p.with_suffix(".tmp.parquet")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, p)


def hash_bucket(uid, prefix, modulus, first8=False):
    digest = hashlib.sha256(f"{prefix}{uid}".encode()).digest()
    return int.from_bytes(digest[:8] if first8 else digest, "big") % modulus


def calibration_user(uid):
    return (0 < uid <= 200948
            and hash_bucket(uid, "feelm-rec-vnext-user-split-v1|", 100, True) <= 59
            and hash_bucket(uid, "rec-ev-022a-user-role-v1|", 10000) <= 5999
            and hash_bucket(uid, "rec-ev-028-user-phase-v1|", 10000) <= 7999
            and hash_bucket(uid, "rec-ev-029-direct-user-split-v1|", 10000) <= 7999)


def user_key(uid):
    return hashlib.sha256(f"rec-ev-022a-user-key-v1|{uid}".encode()).hexdigest()


def rating_index(raw):
    value = float(raw)
    idx = int(round(value * 2)) - 1
    if idx < 0 or idx > 9 or value != (idx + 1) / 2:
        raise ValueError("rating must be on the half-star grid")
    return idx


def profile_weights(indices, prior):
    idx = np.asarray(indices, dtype=np.int64)
    if idx.ndim != 1 or len(idx) == 0 or np.any((idx < 0) | (idx > 9)):
        raise ValueError("invalid rating indices")
    h = np.bincount(idx, minlength=10)
    below = np.cumsum(h) - h
    return 2 * (below[idx] + .5 * h[idx] + 5 * np.asarray(prior)[idx]) / (len(idx) + 5) - 1


def scan_calibration(lines, allowed, max_movie, progress=None):
    """Discard disallowed users BEFORE interpreting movie/rating/timestamp."""
    counts = np.zeros(max_movie + 1, dtype=np.int64)
    sums = np.zeros(max_movie + 1, dtype=np.float64)
    hist = np.zeros((len(allowed), 10), dtype=np.uint32)
    rows = parsed = discarded = 0
    for raw in lines:
        rows += 1
        if progress and rows % 1000000 == 0:
            progress(rows, parsed)
        first = raw.find(b",")
        if first <= 0:
            raise ValueError("malformed user field")
        uid = int(raw[:first])
        if not 0 < uid < len(allowed):
            raise ValueError("unexpected user ID")
        if not allowed[uid]:
            discarded += 1
            continue
        second = raw.find(b",", first + 1)
        third = raw.find(b",", second + 1)
        if second <= first or third <= second:
            raise ValueError("malformed allowed row")
        movie = int(raw[first + 1:second])
        if not 0 < movie <= max_movie:
            raise ValueError("movie outside MovieLens ID bound")
        idx = rating_index(raw[second + 1:third])
        counts[movie] += 1
        sums[movie] += (idx + 1) / 2
        hist[uid, idx] += 1
        parsed += 1
    return counts, sums, hist, {"raw_rows": rows, "calibration_ratings_parsed": parsed,
                               "noncalibration_rows_discarded_after_user_id": discarded,
                               "noncalibration_ratings_parsed": 0, "timestamps_parsed": 0}


def ranked(scores, item_ids, seen_positions=()):
    """Use int64 positions: int16 overflows on the 85,517-item catalogue."""
    values = np.asarray(scores, dtype=np.float64).copy()
    if values.shape != np.asarray(item_ids).shape or not np.isfinite(values).all():
        raise ValueError("finite catalogue score vector required")
    seen = np.asarray(seen_positions, dtype=np.int64)
    values[seen] = -np.inf
    order = np.lexsort((item_ids, -values)).astype(np.int64)
    return order[np.isfinite(values[order])]


def union_candidates(a, b, budget):
    if budget < 2 or budget % 2 or len(a) < budget or len(b) < budget:
        raise ValueError("even budget and sufficiently long rankings required")
    result, present = [], set()
    def add(value):
        x = int(value)
        if x not in present and len(result) < budget:
            present.add(x)
            result.append(x)
    half = budget // 2
    for x in a[:half]:
        add(x)
    for x in b[:half]:
        add(x)
    j = half
    while len(result) < budget:
        if j >= min(len(a), len(b)):
            raise ValueError("not enough unique candidates")
        add(a[j]); add(b[j]); j += 1
    return np.asarray(result, dtype=np.int64)


def fused_order(a, b, item_ids, budget=500, c=60.):
    candidates = union_candidates(a, b, budget)
    ra = np.zeros(len(item_ids), dtype=np.int64)
    rb = np.zeros(len(item_ids), dtype=np.int64)
    ra[a] = np.arange(1, len(a) + 1)
    rb[b] = np.arange(1, len(b) + 1)
    if np.any(ra[candidates] == 0) or np.any(rb[candidates] == 0):
        raise ValueError("candidate missing from component ranking")
    score = 1 / (c + ra[candidates]) + 1 / (c + rb[candidates])
    return candidates[np.lexsort((item_ids[candidates], -score))]


def retrieval_metrics(top, targets, q):
    top = np.asarray(top)
    targets, q = np.asarray(targets), np.asarray(q, dtype=float)
    if len(set(map(int, targets))) != len(targets) or q.shape != targets.shape:
        raise ValueError("target identity mismatch")
    if not np.isfinite(q).all() or np.any((q < 0) | (q > 1)):
        raise ValueError("invalid target utility")
    ranks = {int(movie): r + 1 for r, movie in enumerate(top)}
    target_ranks = np.asarray([ranks.get(int(movie), len(top) + 1) for movie in targets])
    pos, neg = q >= .8, q <= .2
    positive_n = int(pos.sum())
    out = {"positive_targets": positive_n}
    for k in (3, 10, 500):
        out[f"recall{k}"] = float(np.sum(pos & (target_ranks <= k)) / positive_n) if positive_n else np.nan
    for k in (3, 10):
        dcg = np.sum(1 / np.log2(target_ranks[pos & (target_ranks <= k)] + 1))
        idcg = np.sum(1 / np.log2(np.arange(1, min(k, positive_n) + 1) + 1))
        out[f"ndcg{k}"] = float(dcg / idcg) if idcg else np.nan
    judged = int(np.sum(target_ranks <= 3))
    bad = int(np.sum(neg & (target_ranks <= 3)))
    out.update(judged3=judged, known_bad3=bad, judged_fraction3=judged / 3,
               known_bad_lower_bound3=bad / 3,
               conditional_bad3=bad / judged if judged else np.nan,
               judged_fraction500=float(np.sum(target_ranks <= 500) / len(top)))
    return out


def mean_or_none(values):
    a = np.asarray(values, dtype=float)
    a = a[np.isfinite(a)]
    return float(a.mean()) if len(a) else None


def paired_interval(a, b, repeats, seed, family=1):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    valid = np.isfinite(a) & np.isfinite(b)
    d = a[valid] - b[valid]
    if not len(d):
        return {"n": 0, "mean": None, "ci": [None, None], "confidence": 1 - .05 / family}
    rng = np.random.default_rng(seed)
    means = np.empty(repeats)
    for start in range(0, repeats, 100):
        n = min(100, repeats - start)
        indices = rng.integers(len(d), size=(n, len(d)), dtype=np.int32)
        means[start:start+n] = d[indices].mean(axis=1)
    alpha = .05 / family
    return {"n": int(len(d)), "mean": float(d.mean()),
            "ci": np.quantile(means, [alpha / 2, 1 - alpha / 2]).tolist(),
            "confidence": 1 - alpha,
            "improved_fraction": float(np.mean(d > 0)), "worse_fraction": float(np.mean(d < 0)),
            "tie_fraction": float(np.mean(d == 0))}


def check_audit(config_path, cfg, audit_path=AUDIT):
    audit = read_json(audit_path)
    actual = {"code_sha256": sha(HERE), "config_sha256": sha(config_path),
              "design_sha256": sha(ROOT / cfg["design_path"])}
    if audit.get("status") != "PASS_FOR_EXPLORATORY_EXECUTION" or any(audit.get(k) != v for k, v in actual.items()):
        raise RuntimeError("independent implementation audit missing or identity changed")
    return actual


def resident_bytes():
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("peak_rss", "rss", "peak_paged", "paged",
                    "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile", "private")]
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        get_process = ctypes.windll.kernel32.GetCurrentProcess
        get_process.restype = wintypes.HANDLE
        get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
        get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not get_memory(get_process(), ctypes.byref(counters), counters.cb):
            raise RuntimeError("cannot enforce process memory limit")
        return int(counters.peak_rss)
    import resource
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * (1 if sys.platform == "darwin" else 1024)


class Run:
    def __init__(self, config_path=DEFAULT):
        self.config_path = Path(config_path)
        self.cfg = read_json(config_path)
        if self.cfg["ks"] != list(KS) or self.cfg["policies"] != list(POLICIES) or self.cfg["candidate_budget"] != 500:
            raise ValueError("unexpected frozen policy grid")
        self.fingerprint = check_audit(self.config_path, self.cfg)
        self.root = ROOT / self.cfg["output_root"]
        self.root.mkdir(parents=True, exist_ok=True)
        self.start = time.monotonic()
        self.phase_start = self.start
        ledger = self.root / "execution-budget.json"
        previous = read_json(ledger) if ledger.exists() else {}
        if previous and previous["fingerprint"] != self.fingerprint:
            raise RuntimeError("execution budget belongs to a different frozen implementation")
        self.previous_seconds = float(previous.get("total_execution_seconds", 0))

    def elapsed(self):
        return self.previous_seconds + time.monotonic() - self.start

    def guard(self):
        if check_audit(self.config_path, self.cfg) != self.fingerprint:
            raise RuntimeError("running implementation identity changed")
        elapsed = self.elapsed()
        peak = resident_bytes()
        write_json(self.root / "execution-budget.json", {"fingerprint": self.fingerprint,
                   "total_execution_seconds": elapsed, "process_peak_resident_bytes": peak})
        if elapsed > 1800:
            raise RuntimeError("30-minute aggregate execution limit reached")
        if peak > 8 * 1024**3:
            raise RuntimeError("8 GiB process memory limit reached")

    def seal(self, name, value):
        # This is the last fallible validation before publishing a completion seal.
        self.guard()
        value.update(seconds=time.monotonic()-self.phase_start,
                     total_execution_seconds=self.elapsed())
        write_json(self.root / name, value)

    def log(self, phase, **kwargs):
        self.guard()
        write_json(self.root / "progress.json", {"phase": phase, "elapsed_seconds": time.monotonic()-self.start, **kwargs})
        print(json.dumps({"phase": phase, **kwargs}), flush=True)

    def source(self, key, phase):
        if key == "labels" and phase != "evaluate":
            raise RuntimeError("labels cannot be opened before evaluate")
        self.guard()
        spec = self.cfg["inputs"][key]
        path = Path(spec["path"])
        if not path.is_absolute():
            path = ROOT / path
        if path.stat().st_size != spec["bytes"] or sha(path) != spec["sha256"]:
            raise RuntimeError(f"source identity mismatch: {key}")
        return path

    def validate_prepared(self):
        self.guard()
        seal = read_json(self.root / "prepare-seal.json")
        if seal["fingerprint"] != self.fingerprint:
            raise RuntimeError("prepared code/design/config identity changed")
        for name, spec in seal["outputs"].items():
            if identity(self.root / name) != spec:
                raise RuntimeError(f"prepared output changed: {name}")
        for key in ("universe", "structured", "profiles", "prior", "membership"):
            self.source(key, "prepare")
        return seal

    def prepare(self):
        self.phase_start = time.monotonic()
        self.guard()
        if (self.root / "prepare-seal.json").exists():
            self.validate_prepared()
            self.log("PREPARE_VERIFIED_EXISTING")
            return
        if (self.root / "prepared.npz").exists() or (self.root / "profiles.parquet").exists():
            raise RuntimeError("unsealed preparation artifacts; preserve and inspect failure")
        sources = {key: self.source(key, "prepare") for key in self.cfg["inputs"] if key != "labels"}
        with np.load(sources["universe"], allow_pickle=False) as f:
            items = f["item_ids"].astype(np.int64)
        if len(items) != self.cfg["expected_items"] or not np.all(np.diff(items) > 0):
            raise RuntimeError("common catalogue identity drift")
        features = sparse.load_npz(sources["structured"])
        norms = np.sqrt(np.asarray(features.multiply(features).sum(axis=1)).ravel())
        if features.shape[0] != len(items) or not np.isfinite(features.data).all() or not np.allclose(norms, 1, atol=1e-5):
            raise RuntimeError("invalid normalized structured catalogue")
        with np.load(sources["prior"], allow_pickle=False) as f:
            prior = f["g0_mid"].copy()
        profiles = pd.read_parquet(sources["profiles"], columns=PROFILE_COLUMNS,
                                   filters=[("cohort", "==", "SELECTION"), ("outer", "==", "R0")])
        profiles = profiles.sort_values("user_key", kind="stable", ignore_index=True)
        if len(profiles) != self.cfg["expected_users"] or profiles.user_key.duplicated().any():
            raise RuntimeError("frozen evaluation cohort drift")
        member_cols = ["outer", "user_key", "profile_movie_ids", "target_movie_ids"]
        members = pd.read_parquet(sources["membership"], columns=member_cols, filters=[("cohort", "==", "SELECTION")])
        unions = {}
        for row in members.itertuples(index=False):
            unions.setdefault(row.user_key, set()).update(map(int, row.target_movie_ids))
        r0 = members.loc[members.outer.eq("R0")].sort_values("user_key", kind="stable", ignore_index=True)
        if r0.user_key.tolist() != profiles.user_key.tolist():
            raise RuntimeError("membership/profile user identity mismatch")
        ids = set(map(int, items))
        for row, original in zip(profiles.itertuples(index=False), r0.itertuples(index=False), strict=True):
            movies = list(map(int, row.profile_movie_ids)); ratings = list(map(int, row.profile_rating_indices))
            targets = list(map(int, row.target_movie_ids))
            if (len(movies) != 30 or len(set(movies)) != 30 or len(ratings) != 30
                or len(targets) != 20 or len(set(targets)) != 20 or not set(movies + targets) <= ids
                or not all(0 <= v < 10 for v in ratings) or set(movies) & unions[row.user_key]
                or movies != list(original.profile_movie_ids) or targets != list(original.target_movie_ids)):
                raise RuntimeError("profile/target/grid firewall failure")
        allowed = np.fromiter((calibration_user(u) for u in range(self.cfg["max_user_id"]+1)), dtype=bool)
        calibration_keys = {user_key(u) for u in np.flatnonzero(allowed)}
        if calibration_keys & set(profiles.user_key):
            raise RuntimeError("evaluation user leaked into calibration")
        self.log("CALIBRATION_SCAN_START", users=len(profiles), items=len(items))
        with zipfile.ZipFile(sources["archive"]) as bundle, bundle.open("ml-32m/ratings.csv") as handle:
            if handle.readline().rstrip(b"\r\n") != b"userId,movieId,rating,timestamp":
                raise RuntimeError("raw rating header drift")
            counts, sums, hist, counters = scan_calibration(handle, allowed, 300000,
                lambda rows, parsed: self.log("CALIBRATION_SCAN", rows=rows, parsed=parsed))
        active = hist.sum(axis=1) > 0
        if (counters["raw_rows"] != self.cfg["expected_raw_rows"] or int(active.sum()) != self.cfg["expected_train_users"]
            or counters["calibration_ratings_parsed"] != self.cfg["expected_train_ratings"]):
            raise RuntimeError("calibration source population/count drift")
        pi0 = (hist[active] / hist[active].sum(axis=1)[:, None]).mean(axis=0)
        pi0 /= pi0.sum()
        if not np.allclose(np.cumsum(pi0)-.5*pi0, prior, atol=1e-12, rtol=0):
            raise RuntimeError("calibration prior does not match permitted users")
        global_mean = float(sums.sum() / counts.sum())
        strength = self.cfg["prior_strength"]
        bayes = (sums[items] + strength * global_mean) / (counts[items] + strength)
        self.guard()
        write_npz(self.root / "prepared.npz", item_ids=items, counts=counts[items], bayes=bayes,
                  prior=prior, global_mean=np.asarray(global_mean))
        self.guard()
        write_parquet(self.root / "profiles.parquet", profiles)
        seal = {"status": "PREPARED_WITHOUT_TARGET_LABELS", "fingerprint": self.fingerprint,
                "reader": counters, "calibration_users": int(active.sum()), "evaluation_users": len(profiles),
                "evaluation_calibration_overlap": 0, "items": len(items), "features": list(features.shape),
                "calibration_count_zero_items": int(np.sum(counts[items] == 0)),
                "labels_opened": False, "protected_data_opened": False,
                "outputs": {name: identity(self.root/name) for name in ("prepared.npz", "profiles.parquet")},
                "seconds": time.monotonic()-self.start,
                "environment": {"python": sys.version, "platform": platform.platform(), "numpy": np.__version__, "pandas": pd.__version__}}
        self.log("PREPARED", calibration_users=int(active.sum()), calibration_ratings=int(counts.sum()))
        self.seal("prepare-seal.json", seal)

    def validate_scores(self):
        self.validate_prepared()
        seal = read_json(self.root / "score-seal.json")
        if seal["fingerprint"] != self.fingerprint or seal["prepare_seal"] != identity(self.root/"prepare-seal.json"):
            raise RuntimeError("score dependency identity changed")
        if seal["rankings"] != identity(self.root/"rankings.npz"):
            raise RuntimeError("sealed rankings changed")
        return seal

    def score(self):
        self.phase_start = time.monotonic()
        self.validate_prepared()
        if (self.root/"score-seal.json").exists():
            self.validate_scores(); self.log("SCORES_VERIFIED_EXISTING"); return
        if (self.root/"rankings.npz").exists():
            raise RuntimeError("unsealed score output exists")
        with np.load(self.root/"prepared.npz", allow_pickle=False) as f:
            items, counts, bayes, prior = [f[k].copy() for k in ("item_ids", "counts", "bayes", "prior")]
        profiles = pd.read_parquet(self.root/"profiles.parquet", columns=["user_key", "profile_movie_ids", "profile_rating_indices"])
        features = sparse.load_npz(self.source("structured", "score")).astype(np.float64).tocsr()
        movies = np.vstack(profiles.profile_movie_ids).astype(np.int64)
        ratings = np.vstack(profiles.profile_rating_indices).astype(np.int8)
        keys = profiles.user_key.to_numpy(dtype="U64")
        lookup = np.full(int(items.max())+1, -1, dtype=np.int64); lookup[items] = np.arange(len(items))
        positions = lookup[movies]
        n = len(keys)
        top = np.zeros((n, len(KS), len(POLICIES), 500), dtype=np.int32)
        fallback = np.zeros((n, len(KS), len(POLICIES)), dtype=bool)
        std = np.zeros((n, len(KS)))
        donor = (np.arange(n) + 1) % n
        component_seconds = {}
        for ki, k in enumerate(KS):
            started = time.monotonic()
            std[:, ki] = np.std((ratings[:, :k] + 1)/2, axis=1)
            weights = np.vstack([profile_weights(row[:k], prior) for row in ratings])
            denominators = np.sum(np.abs(weights), axis=1)
            safe = denominators > 1e-12
            normalized = np.divide(weights, denominators[:, None], out=np.zeros_like(weights), where=safe[:, None])
            h = sparse.csr_matrix((normalized.ravel(), (np.repeat(np.arange(n), k), positions[:, :k].ravel())), shape=(n, len(items)))
            vectors = (h @ features).tocsr()
            active = safe & (np.sqrt(np.asarray(vectors.multiply(vectors).sum(axis=1)).ravel()) > 1e-12)
            for start in range(0, n, 32):
                stop = min(n, start+32)
                source_rows = np.append(np.arange(start, stop), donor[stop-1])
                scores = (features @ vectors[source_rows].T).toarray()
                if not np.isfinite(scores).all():
                    raise RuntimeError("nonfinite catalogue score")
                for local, u in enumerate(range(start, stop)):
                    seen = positions[u, :k]
                    a = ranked(bayes, items, seen); count_order = ranked(counts, items, seen)
                    c = ranked(scores[:, local], items, seen) if active[u] else a
                    shuffled = ranked(scores[:, local+1], items, seen) if active[donor[u]] else a
                    combined = fused_order(a, c, items, c=self.cfg["rrf_c"])
                    orders = (a[:500], count_order[:500], c[:500], combined, shuffled[:500])
                    for pi, order in enumerate(orders):
                        if len(order) != 500 or len(np.unique(order)) != 500 or np.intersect1d(order, seen).size:
                            raise RuntimeError("candidate budget/seen leakage")
                        top[u, ki, pi] = items[order]
                    fallback[u, ki, 2:4] = not active[u]
                    fallback[u, ki, 4] = not active[donor[u]]
                if start % 256 == 0:
                    self.log("SCORING", k=k, users_complete=stop, users_total=n)
            component_seconds[str(k)] = time.monotonic()-started
        self.guard()
        write_npz(self.root/"rankings.npz", user_keys=keys, ks=np.asarray(KS), policies=np.asarray(POLICIES),
                  candidates=top, fallback=fallback, input_std=std, donor_indices=donor)
        self.log("SCORING_COMPLETE", cells=n*len(KS)*len(POLICIES))
        self.seal("score-seal.json", {"status": "SCORED_ALL_POLICIES_BEFORE_LABELS", "fingerprint": self.fingerprint,
                   "prepare_seal": identity(self.root/"prepare-seal.json"), "rankings": identity(self.root/"rankings.npz"),
                   "users": n, "ks": list(KS), "policies": list(POLICIES), "candidate_budget": 500,
                   "seconds_by_k": component_seconds, "seconds": time.monotonic()-self.start,
                   "labels_opened": False, "protected_data_opened": False})

    def evaluate(self):
        self.phase_start = time.monotonic()
        score_seal = self.validate_scores()  # Must complete before ANY label path is opened.
        if (self.root/"evaluation-seal.json").exists():
            previous = read_json(self.root/"evaluation-seal.json")
            if previous["fingerprint"] != self.fingerprint or previous["score_seal"] != identity(self.root/"score-seal.json"):
                raise RuntimeError("completed evaluation dependencies changed")
            for name, spec in previous["outputs"].items():
                if identity(self.root/name) != spec:
                    raise RuntimeError("completed evaluation output changed")
            if previous["labels"] != identity(self.source("labels", "evaluate")):
                raise RuntimeError("completed evaluation label identity changed")
            self.log("EVALUATION_VERIFIED_EXISTING")
            return
        with np.load(self.root/"rankings.npz", allow_pickle=False) as f:
            keys, top, fallbacks, std = [f[k].copy() for k in ("user_keys", "candidates", "fallback", "input_std")]
        with np.load(self.root/"prepared.npz", allow_pickle=False) as f:
            items, counts = f["item_ids"].copy(), f["counts"].copy()
        label_path = self.source("labels", "evaluate")
        self.guard()
        labels = pd.read_parquet(label_path, columns=LABEL_COLUMNS, filters=[("outer", "==", "R0")])
        labels = labels.sort_values("user_key", kind="stable", ignore_index=True)
        profiles = pd.read_parquet(self.root/"profiles.parquet")
        if labels.user_key.tolist() != keys.tolist():
            raise RuntimeError("score/label user alignment mismatch")
        for row, prof in zip(labels.itertuples(index=False), profiles.itertuples(index=False), strict=True):
            if list(row.target_movie_ids) != list(prof.target_movie_ids):
                raise RuntimeError("score/label target identity mismatch")
        rows = []
        target_counts = {int(movie): int(count) for movie, count in zip(items, counts, strict=True)}
        for u, row in enumerate(labels.itertuples(index=False)):
            if u % 256 == 0:
                self.log("EVALUATING", users_complete=u, users_total=len(keys))
            tq = np.asarray(row.target_q, dtype=float)
            targets = np.asarray(row.target_movie_ids, dtype=np.int64)
            tc = np.asarray([target_counts[int(movie)] for movie in targets])
            for ki, k in enumerate(KS):
                for pi, policy in enumerate(POLICIES):
                    metrics = retrieval_metrics(top[u, ki, pi], targets, tq)
                    record = {"user_key": keys[u], "k": k, "policy": policy, "fallback": bool(fallbacks[u, ki, pi]),
                              "input_std": float(std[u, ki]), **metrics}
                    for name, mask in (("count0",tc==0),("count1_9",(tc>=1)&(tc<10)),("count10_99",(tc>=10)&(tc<100)),("count100plus",tc>=100)):
                        positives = targets[mask & (tq >= .8)]
                        record[f"recall500_{name}"] = float(np.isin(positives,top[u,ki,pi]).mean()) if len(positives) else np.nan
                    rows.append(record)
        metrics = pd.DataFrame(rows)
        self.guard()
        write_parquet(self.root/"user-metrics.parquet", metrics)
        cells = {}
        for (k, policy), group in metrics.groupby(["k","policy"],sort=True):
            ki, pi = KS.index(int(k)), POLICIES.index(policy)
            numeric = [c for c in group if c not in ("user_key","k","policy")]
            means = {c:mean_or_none(group[c]) for c in numeric}
            means.update(users=len(group), primary_eligible_users=int(group.recall500.notna().sum()),
                         judged3_occurrences=int(group.judged3.sum()), known_bad3_occurrences=int(group.known_bad3.sum()),
                         conditional_bad3_pooled=float(group.known_bad3.sum()/group.judged3.sum()) if group.judged3.sum() else None,
                         catalog_coverage3=float(len(np.unique(top[:,ki,pi,:3]))/len(items)),
                         catalog_coverage500=float(len(np.unique(top[:,ki,pi]))/len(items)))
            cells[f"{policy}_K{k}"] = means
        def values(k,p,metric):
            return metrics.loc[metrics.k.eq(k)&metrics.policy.eq(p)].sort_values("user_key")[metric].to_numpy()
        def contrast(k,a,b,metric,family=1):
            self.guard()
            return paired_interval(values(k,a,metric),values(k,b,metric), self.cfg["bootstrap_repeats"], self.cfg["bootstrap_seed"],family)
        primary = {f"{p}_VS_{b}":contrast(30,p,b,"recall500",4) for p in ("CONTENT","UNION") for b in ("BAYES","COUNT")}
        diagnostics = {}
        for k in KS:
            for a,b in (("CONTENT","BAYES"),("UNION","BAYES"),("CONTENT","SHUFFLE")):
                for metric in ("recall500","recall3","ndcg3","judged_fraction3","known_bad_lower_bound3"):
                    diagnostics[f"K{k}_{a}_VS_{b}_{metric}"] = contrast(k,a,b,metric)
        for p in ("CONTENT","UNION"):
            diagnostics[f"{p}_K30_VS_K10_recall500"] = paired_interval(values(30,p,"recall500"),values(10,p,"recall500"),self.cfg["bootstrap_repeats"],self.cfg["bootstrap_seed"])
        decisions = {}
        for policy in ("CONTENT","UNION"):
            effects = [primary[f"{policy}_VS_{b}"] for b in ("BAYES","COUNT")]
            if all(e["n"] and e["ci"][0]>0 and e["mean"]>=self.cfg["screen_minimum_recall_gain"] for e in effects):
                decisions[policy] = "CANDIDATE_GENERATION_FOLLOWUP_ONLY"
            elif all(e["n"] and e["ci"][1]<0 for e in effects):
                decisions[policy] = "REJECT_AS_STANDALONE_CANDIDATE_GENERATOR" if policy=="CONTENT" else "REJECT_THIS_UNION_RULE"
            else:
                decisions[policy] = "INSUFFICIENT_EFFECT_OR_PRECISION_FOR_SCREEN"
        dispersion = {}
        for (k,p),group in metrics.groupby(["k","policy"],sort=True):
            for name,mask in (("std_le_0.5",group.input_std<=.5),("std_gt_0.5_le_1",(group.input_std>.5)&(group.input_std<=1)),("std_gt_1",group.input_std>1)):
                dispersion[f"{p}_K{k}_{name}"]={"users":int(mask.sum()),"recall500":mean_or_none(group.loc[mask,"recall500"])}
        result={"experiment_id":"REC-EV-031","claim":"EXPLORATORY_PREVIOUSLY_USED_SELECTION_R0",
                "users":len(keys),"catalogue_items":len(items),"primary":"macro known-positive Recall@500 at K30",
                "primary_eligible_users":cells["BAYES_K30"]["primary_eligible_users"],
                "cohort_retention":{"r0_before_profile30_filter":2511,"retained":2180,"fraction":2180/2511},
                "cells":cells,"primary_contrasts":primary,"diagnostics":diagnostics,"input_dispersion":dispersion,
                "decisions":decisions,"fresh_confirmation":False,"product_policy_changed":False,"protected_data_opened":False,
                "label_projection":LABEL_COLUMNS,"label_filter":{"outer":"R0"},
                "inference_scope":"user bootstrap conditional on fixed R0 target selection and common catalogue; no movie-generalization or actual satisfaction claim",
                "score_seal":identity(self.root/"score-seal.json"),"metrics":identity(self.root/"user-metrics.parquet"),
                "seconds":time.monotonic()-self.start}
        self.guard()
        write_json(self.root/"metrics.json",result)
        self.cases(keys,top,labels,profiles)
        self.log("EVALUATED",decisions=decisions)
        self.seal("evaluation-seal.json",{"status":"EVALUATED_PREVIOUSLY_OPENED_SELECTION_LABELS",
                   "fingerprint":self.fingerprint,"score_seal":identity(self.root/"score-seal.json"),
                   "labels":identity(label_path),"outputs":{name:identity(self.root/name) for name in ("metrics.json","user-metrics.parquet","cases.json")},
                   "protected_data_opened":False,"fresh_confirmation":False})

    def cases(self,keys,top,labels,profiles):
        chosen = sorted(range(len(keys)),key=lambda i:hashlib.sha256(f"rec-ev-031-case-v1|{keys[i]}".encode()).digest())[:3]
        titles={}
        archive=self.source("archive","evaluate")
        with zipfile.ZipFile(archive) as z, z.open("ml-32m/movies.csv") as f:
            for row in csv.DictReader(io.TextIOWrapper(f,encoding="utf-8")):
                titles[int(row["movieId"])]=row["title"]
        cases=[]
        for i in chosen:
            row=labels.iloc[i]
            known=dict(zip(map(int,row.target_movie_ids),map(float,row.target_q),strict=True))
            case={"user_key":str(keys[i]),"selection":"fixed hash before quality inspection","lists":{}}
            for ki,k in enumerate(KS):
                for pi,p in enumerate(POLICIES):
                    case["lists"][f"{p}_K{k}"]=[{"movie_id":int(m),"title":titles.get(int(m),str(m)),
                       "observed_target_q":known.get(int(m)),"judgment":"OBSERVED_TARGET" if int(m) in known else "UNKNOWN"} for m in top[i,ki,pi,:3]]
            cases.append(case)
        self.guard()
        write_json(self.root/"cases.json",cases)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--phase",choices=["prepare","score","evaluate","all"],required=True)
    parser.add_argument("--config",type=Path,default=DEFAULT)
    args=parser.parse_args()
    run=None
    try:
        run=Run(args.config)
        phases=("prepare","score","evaluate") if args.phase=="all" else (args.phase,)
        for phase in phases:
            getattr(run,phase)()
    except Exception as exc:
        if run is not None:
            path=run.root/"attempts.jsonl"
            with path.open("a",encoding="utf-8") as f:
                f.write(json.dumps({"phase":args.phase,"status":"FAILED","error_type":type(exc).__name__,"error":str(exc),"fingerprint":run.fingerprint})+"\n")
        traceback.print_exc()
        raise SystemExit(1)


if __name__=="__main__":
    main()
