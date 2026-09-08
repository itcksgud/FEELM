"""REC032: one fixed research hybrid, sealed before exploratory label reuse."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import zipfile

# Small matvecs should not start a thread per CPU per user.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import psutil
from scipy import sparse

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs/recommendation/experiments/rec-ev-032"
FILES = {"design": PLAN / "README.md", "config": PLAN / "config.json",
         "runner": Path(__file__).resolve(),
         "tests": ROOT / "scripts/tests/test_rec_ev_032_basic.py"}
OUTERS = ("R0", "R1", "R2", "R3", "R4", "KR", "RECENT")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def pin(path):
    return {"bytes": Path(path).stat().st_size, "sha256": sha(path)}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def fingerprint():
    return {name: sha(path) for name, path in FILES.items()}


def check_review():
    review = read_json(PLAN / "review.json")
    actual = fingerprint()
    require(review["status"] == "PASS" and review["fingerprint"] == actual,
            "independent review absent, failed, or stale")
    return actual


def bucket(uid, prefix, modulus, first8=False):
    value = hashlib.sha256(f"{prefix}{uid}".encode()).digest()
    return int.from_bytes(value[:8] if first8 else value, "big") % modulus


def calibration_user(uid):
    return (0 < uid <= 200948
            and bucket(uid, "feelm-rec-vnext-user-split-v1|", 100, True) <= 59
            and bucket(uid, "rec-ev-022a-user-role-v1|", 10000) <= 5999
            and bucket(uid, "rec-ev-028-user-phase-v1|", 10000) <= 7999
            and bucket(uid, "rec-ev-029-direct-user-split-v1|", 10000) <= 7999)


def user_key(uid):
    return hashlib.sha256(f"rec-ev-022a-user-key-v1|{uid}".encode()).hexdigest()


def rating_index(raw):
    value = float(raw)
    require(np.isfinite(value) and 1 <= value * 2 <= 10 and value * 2 == int(value * 2),
            "rating outside exact half-star grid")
    return int(value * 2) - 1


def filtered_rating(raw, allowed, max_movie):
    first = raw.find(b",")
    require(first > 0, "invalid user field")
    uid = int(raw[:first])
    require(0 < uid < len(allowed), "invalid user ID")
    if not allowed[uid]:
        return None  # Do not interpret disallowed movie/rating/timestamp.
    second = raw.find(b",", first + 1)
    third = raw.find(b",", second + 1)
    require(second > first and third > second, "invalid allowed row")
    movie = int(raw[first + 1:second])
    require(0 < movie <= max_movie, "invalid movie ID")
    return uid, movie, rating_index(raw[second + 1:third])


def prior_from_hist(hist):
    totals = hist.sum(axis=1)
    active = totals > 0
    require(active.any(), "empty training population")
    pi = (hist[active] / totals[active, None]).mean(axis=0)
    pi /= pi.sum()
    return np.cumsum(pi) - .5 * pi


def weights(indices, prior):
    indices = np.asarray(indices, dtype=np.int64)
    require(indices.ndim == 1 and len(indices) > 0 and np.all((indices >= 0) & (indices < 10)),
            "invalid input indices")
    h = np.bincount(indices, minlength=10)
    below = np.cumsum(h) - h
    return 2 * (below[indices] + .5 * h[indices] + 5 * prior[indices]) / (len(indices) + 5) - 1


def order(scores, ids, allowed=None):
    values = np.asarray(scores, dtype=np.float64)
    mask = np.isfinite(values) if allowed is None else np.asarray(allowed, dtype=bool) & np.isfinite(values)
    positions = np.flatnonzero(mask).astype(np.int64)
    return positions[np.lexsort((np.asarray(ids)[positions], -values[positions]))]


def fold_in(factors, raw_ratings, reg):
    y = np.asarray(factors, dtype=np.float64)
    r = np.asarray(raw_ratings, dtype=np.float64)
    require(y.ndim == 2 and r.shape == (len(y),) and np.isfinite(y).all(), "fold-in shape/values")
    for value in r:
        rating_index(value)
    if len(y) == 0:
        return None
    return np.linalg.solve(y.T @ y + reg * len(y) * np.eye(y.shape[1]), y.T @ r)


def rrf(orders, size, c=60.):
    scores = np.zeros(size, dtype=np.float64)
    for component in orders:
        component = np.asarray(component, dtype=np.int64)
        require(len(set(component)) == len(component) and np.all((component >= 0) & (component < size)),
                "invalid partial component ranking")
        scores[component] += 1 / (c + np.arange(1, len(component) + 1))
    return scores


def choose_hybrid(component_orders, p0_order, ids, top_n=2):
    components = [x for x in component_orders if len(x)]
    if not components:
        return np.asarray(p0_order[:top_n], dtype=np.int64), np.zeros(len(ids)), "P0_NO_COMPONENT"
    score = rrf(components, len(ids))
    selected = order(score, ids, score > 0)[:top_n].tolist()
    reason = "NONE"
    if len(selected) < top_n:
        reason = "P0_SUPPLY_TOPUP"
        for pos in p0_order:
            if int(pos) not in selected:
                selected.append(int(pos))
            if len(selected) == top_n:
                break
    return np.asarray(selected, dtype=np.int64), score, reason


def q_from_hist(index, histogram):
    hist = np.asarray(histogram, dtype=np.int64)
    require(hist.shape == (10,) and (hist >= 0).all() and hist.sum() > 0, "invalid fixed H")
    require(0 <= index < 10, "invalid label rating")
    return float((hist[:index].sum() + .5 * hist[index]) / hist.sum())


def metric_bounds(q):
    require(len(q) == 2, "two supplied slots required")
    known = [float(v) for v in q if v is not None]
    require(all(np.isfinite(v) and 0 <= v <= 1 for v in known), "invalid Q")
    missing = 2 - len(known)
    mean = (sum(known) / 2, (sum(known) + missing) / 2)
    minimum = (0., min(known) if known else 1.) if missing else (min(known), min(known))
    harm = (1., 1.) if any(v <= .2 for v in known) else ((0., 1.) if missing else (0., 0.))
    return np.array([mean, minimum, harm], dtype=np.float64)


def paired_bounds(movies_m, movies_p, labels):
    require(len(movies_m) == len(movies_p) == 2, "two supplied movies")
    require(len(set(movies_m)) == len(set(movies_p)) == 2, "duplicate output movie")
    if set(movies_m) == set(movies_p):
        return np.zeros((3, 2))
    m = metric_bounds([labels.get(int(i)) for i in movies_m])
    p = metric_bounds([labels.get(int(i)) for i in movies_p])
    difference = np.column_stack([m[:, 0] - p[:, 1], m[:, 1] - p[:, 0]])
    low = high = 0.
    for movie in set(movies_m) | set(movies_p):
        coefficient = (int(movie in movies_m) - int(movie in movies_p)) / 2
        q = labels.get(int(movie))
        if q is None:
            low += min(coefficient, 0); high += max(coefficient, 0)
        else:
            low += coefficient * q; high += coefficient * q
    difference[0] = (low, high)
    return difference


def ci_bounds(values, repeats, seed, alpha, family):
    # values: user x n x metric x (lower,upper); shared user draws for every cell.
    rng = np.random.default_rng(seed)
    samples = []
    for offset in range(0, repeats, 50):
        draw = rng.integers(0, len(values), size=(min(50, repeats - offset), len(values)))
        samples.append(values[draw].mean(axis=1))
    samples = np.concatenate(samples)
    tail = alpha / (2 * family)
    return np.stack([np.quantile(samples[..., 0], tail, axis=0),
                     np.quantile(samples[..., 1], 1 - tail, axis=0)], axis=-1)


def check_remaining_budget(previous, failure, config):
    require(failure.get("status") != "RESOURCE_LIMIT", "resource-limited run cannot resume")
    require(previous.get("seconds", 0.) < config["max_seconds"]
            and previous.get("peak_tree_rss_bytes", 0) <= config["max_process_tree_bytes"],
            "execution budget already exhausted")


class Run:
    def __init__(self):
        self.identity = check_review()
        self.cfg = read_json(FILES["config"])
        c = self.cfg
        require(c["ns"] == [0, 5, 10, 30] and c["top_n"] == 2 and c["rrf_c"] == 60
                and c["input_prior"] == 5 and c["popularity_prior"] == 50
                and c["allowed_label_outers"] == list(OUTERS), "frozen experiment drift")
        self.root = ROOT / c["output_root"]
        require(self.root.resolve().is_relative_to((ROOT / "outputs/recommendation-evidence").resolve()),
                "output must stay in research outputs")
        self.root.mkdir(parents=True, exist_ok=True)
        previous = read_json(self.root / "budget.json") if (self.root / "budget.json").exists() else {}
        require(not previous or previous["fingerprint"] == self.identity, "different partial execution identity")
        failure = read_json(self.root / "failure.json") if (self.root / "failure.json").exists() else {}
        check_remaining_budget(previous, failure, c)
        self.previous = previous.get("seconds", 0.)
        self.start = time.monotonic()
        self.peak = previous.get("peak_tree_rss_bytes", 0)
        self.budget_lock = threading.Lock()
        self.done = threading.Event()
        self.process = psutil.Process()
        self.watch = threading.Thread(target=self.watchdog, daemon=True)
        self.watch.start()

    def elapsed(self):
        return self.previous + time.monotonic() - self.start

    def save_budget(self, resource_failure=None):
        with self.budget_lock:
            seconds = self.elapsed()
            write_json(self.root / "budget.json", {"fingerprint": self.identity, "seconds": seconds,
                                                   "peak_tree_rss_bytes": self.peak})
            if resource_failure is not None:
                write_json(self.root / "failure.json", {"status": "RESOURCE_LIMIT", "seconds": seconds,
                           "tree_rss_bytes": resource_failure, "fingerprint": self.identity})

    def watchdog(self):
        while not self.done.wait(2):
            try:
                children = self.process.children(recursive=True)
                memory = self.process.memory_info().rss
                for child in children:
                    try:
                        memory += child.memory_info().rss
                    except psutil.NoSuchProcess:
                        pass
                self.peak = max(self.peak, memory)
                if self.elapsed() > self.cfg["max_seconds"] or memory > self.cfg["max_process_tree_bytes"]:
                    self.save_budget(resource_failure=memory)
                    for child in reversed(children):
                        try:
                            child.terminate()
                        except psutil.NoSuchProcess:
                            pass
                    os._exit(70)
            except psutil.NoSuchProcess:
                return

    def guard(self):
        require(check_review() == self.identity, "review/code changed during run")
        if self.elapsed() > self.cfg["max_seconds"] or self.peak > self.cfg["max_process_tree_bytes"]:
            self.save_budget(resource_failure=self.peak)
            raise RuntimeError("resource limit")
        self.save_budget()

    def log(self, phase, **values):
        self.guard()
        print(json.dumps({"phase": phase, "seconds": round(self.elapsed(), 2), **values}), flush=True)

    def source(self, name, phase):
        require(name != "labels" or phase == "evaluate", "label access before score seal")
        spec = self.cfg["inputs"][name]
        p = Path(spec["path"])
        if not p.is_absolute():
            p = ROOT / p
        require(pin(p) == {"bytes": spec["bytes"], "sha256": spec["sha256"]}, f"source mismatch: {name}")
        return p

    def fresh(self, names):
        require(not any((self.root / name).exists() for name in names), "partial output exists; preserve and inspect")

    def seal(self, phase, names, **values):
        self.guard()
        result = {"status": "COMPLETE", "phase": phase, "fingerprint": self.identity,
                  "outputs": {name: pin(self.root / name) for name in names}, **values}
        self.guard()
        write_json(self.root / f"{phase}-seal.json", result)

    def validate(self, phase):
        self.guard()
        result = read_json(self.root / f"{phase}-seal.json")
        require(result["status"] == "COMPLETE" and result["fingerprint"] == self.identity, "stale phase seal")
        for name, identity in result["outputs"].items():
            require(pin(self.root / name) == identity, "modified sealed output: " + name)
        return result

    def prepare(self):
        names = ["training.parquet", "prepared.npz", "profiles.parquet", "evaluation-membership.parquet"]
        if (self.root / "prepare-seal.json").exists():
            self.validate("prepare"); return
        self.fresh(names)
        src = {key: self.source(key, "prepare") for key in self.cfg["inputs"] if key != "labels"}
        with np.load(src["universe"], allow_pickle=False) as z:
            ids = z["item_ids"].astype(np.int64)
        require(len(ids) == self.cfg["expected_items"] and np.all(np.diff(ids) > 0), "catalogue identity")
        x = sparse.load_npz(src["structured"])
        norms = np.sqrt(np.asarray(x.multiply(x).sum(axis=1)).ravel())
        require(x.shape[0] == len(ids) and np.isfinite(x.data).all() and np.allclose(norms, 1, atol=1e-5),
                "content/index/normalization")
        cols = ["user_key", "profile_movie_ids", "profile_rating_indices"]
        profiles = pd.read_parquet(src["profiles"], columns=cols,
                                   filters=[("cohort", "==", "SELECTION"), ("outer", "==", "R0")])
        profiles = profiles.sort_values("user_key", kind="stable", ignore_index=True)
        require(len(profiles) == self.cfg["expected_users"] and not profiles.user_key.duplicated().any(), "cohort")
        members = pd.read_parquet(src["membership"],
                    columns=["outer", "user_key", "profile_movie_ids", "target_movie_ids"],
                    filters=[("cohort", "==", "SELECTION")])
        members = members[members.user_key.isin(profiles.user_key)].copy()
        require(set(members.outer) <= set(OUTERS) and not members.duplicated(["outer", "user_key"]).any(),
                "membership roles")
        unions = {}
        for row in members.itertuples(index=False):
            unions.setdefault(row.user_key, set()).update(map(int, row.target_movie_ids))
        r0 = members[members.outer.eq("R0")].set_index("user_key")
        allowed = np.fromiter((calibration_user(uid) for uid in range(self.cfg["max_user_id"] + 1)), dtype=bool)
        calibration_keys = {user_key(int(uid)) for uid in np.flatnonzero(allowed)}
        require(not calibration_keys.intersection(profiles.user_key), "training/evaluation overlap")
        catalogue_ids = set(ids)
        for row in profiles.itertuples(index=False):
            movies = list(map(int, row.profile_movie_ids)); indices = list(map(int, row.profile_rating_indices))
            require(len(movies) == len(set(movies)) == len(indices) == 30
                    and all(0 <= i < 10 for i in indices) and set(movies) <= catalogue_ids
                    and movies == list(r0.loc[row.user_key, "profile_movie_ids"])
                    and not set(movies).intersection(unions[row.user_key]), "O/E/grid/index")
        counts = np.zeros(self.cfg["max_movie_id"] + 1, dtype=np.int64)
        sums = np.zeros_like(counts, dtype=np.float64)
        hist = np.zeros((len(allowed), 10), dtype=np.uint32)
        schema = pa.schema([("user_id", pa.int32()), ("movie_id", pa.int32()), ("rating", pa.float32())])
        temporary = self.root / "training.parquet.tmp"
        require(not temporary.exists(), "partial training extraction exists")
        row_count = parsed = 0
        users, movies, ratings = [], [], []
        def flush(writer):
            writer.write_table(pa.Table.from_arrays([pa.array(users, type=pa.int32()),
                pa.array(movies, type=pa.int32()), pa.array(ratings, type=pa.float32())], schema=schema))
            users.clear(); movies.clear(); ratings.clear()
        self.log("CALIBRATION_EXTRACT")
        with pq.ParquetWriter(temporary, schema) as writer:
            with zipfile.ZipFile(src["archive"]) as bundle, bundle.open("ml-32m/ratings.csv") as source:
                require(source.readline().rstrip(b"\r\n") == b"userId,movieId,rating,timestamp", "CSV schema")
                for line in source:
                    row_count += 1
                    value = filtered_rating(line, allowed, self.cfg["max_movie_id"])
                    if value is not None:
                        uid, movie, index = value
                        rating = (index + 1) / 2
                        users.append(uid); movies.append(movie); ratings.append(rating)
                        hist[uid, index] += 1; counts[movie] += 1; sums[movie] += rating; parsed += 1
                        if len(users) >= 100000:
                            flush(writer)
                    if row_count % 4000000 == 0:
                        self.log("CALIBRATION_PROGRESS", scanned=row_count, allowed_rows=parsed)
                if users:
                    flush(writer)
        active = hist.sum(axis=1) > 0
        require(row_count == self.cfg["expected_raw_rows"] and parsed == self.cfg["expected_train_ratings"]
                and int(active.sum()) == self.cfg["expected_train_users"], "training source role/count drift")
        prior = prior_from_hist(hist)
        with np.load(src["prior"], allow_pickle=False) as z:
            require(np.allclose(prior, z["g0_mid"], atol=1e-12, rtol=0), "prior population mismatch")
        mu = sums.sum() / counts.sum()
        bayes = (sums[ids] + 50 * mu) / (counts[ids] + 50)
        os.replace(temporary, self.root / "training.parquet")
        np.savez_compressed(self.root / "prepared.npz", item_ids=ids, bayes=bayes, prior=prior,
                            counts=counts[ids], training_user_ids=np.flatnonzero(active))
        profiles.to_parquet(self.root / "profiles.parquet", index=False)
        members.to_parquet(self.root / "evaluation-membership.parquet", index=False)
        self.seal("prepare", names, calibration_users=int(active.sum()), calibration_ratings=parsed,
                  raw_rows=row_count, evaluation_users=len(profiles), overlap=0, timestamps_parsed=0,
                  noncalibration_rating_values_parsed=0, label_payload_opened=False,
                  content_shape=list(x.shape), sources={name: self.cfg["inputs"][name] for name in src})
        self.log("PREPARED")

    def train(self):
        prepared = self.validate("prepare")
        if (self.root / "train-seal.json").exists():
            self.validate("train"); return
        self.fresh(["factors.npz"])
        from pyspark.ml.recommendation import ALS
        from pyspark.sql import SparkSession
        c = self.cfg["als"]
        self.log("ALS_TRAIN_START", train_rows=prepared["calibration_ratings"])
        spark = (SparkSession.builder.master(c["master"]).appName("feelm-rec-ev-032")
                 .config("spark.driver.memory", c["driver_memory"])
                 .config("spark.sql.shuffle.partitions", str(c["shuffle_partitions"]))
                 .config("spark.ui.enabled", "false").config("spark.ui.showConsoleProgress", "false")
                 .getOrCreate())
        try:
            spark.sparkContext.setLogLevel("ERROR")
            frame = spark.read.parquet(str((self.root / "training.parquet").resolve()))
            require(frame.count() == self.cfg["expected_train_ratings"], "ALS training row mismatch")
            estimator = ALS(rank=c["rank"], regParam=c["reg"], maxIter=c["iterations"], seed=c["seed"],
                userCol="user_id", itemCol="movie_id", ratingCol="rating", implicitPrefs=False,
                nonnegative=False, coldStartStrategy="nan",
                numUserBlocks=c["num_user_blocks"], numItemBlocks=c["num_item_blocks"])
            model = estimator.fit(frame)
            factors = model.itemFactors.toPandas().sort_values("id")
            ids = factors.id.to_numpy(dtype=np.int64)
            y = np.asarray(factors.features.tolist(), dtype=np.float64)
            require(y.shape == (len(ids), c["rank"]) and np.isfinite(y).all()
                    and np.all(np.diff(ids) > 0), "invalid learned factors")
            version = spark.version
        finally:
            spark.stop()
        np.savez_compressed(self.root / "factors.npz", item_ids=ids, factors=y)
        self.seal("train", ["factors.npz"], prepare_seal=pin(self.root / "prepare-seal.json"),
                  training_rows=self.cfg["expected_train_ratings"], training_users=self.cfg["expected_train_users"],
                  factor_items=len(ids), spark=version, numpy=np.__version__, pandas=pd.__version__, python=sys.version)
        self.log("ALS_TRAIN_COMPLETE", factor_items=len(ids))

    def score(self):
        self.validate("prepare")
        train = self.validate("train")
        require(train["prepare_seal"] == pin(self.root / "prepare-seal.json"), "training dependency drift")
        if (self.root / "score-seal.json").exists():
            self.validate("score"); return
        self.fresh(["rankings.npz", "score-diagnostics.parquet"])
        feature_path = self.source("structured", "score")
        x = sparse.load_npz(feature_path).astype(np.float64).tocsr()
        with np.load(self.root / "prepared.npz", allow_pickle=False) as z:
            ids, bayes, prior = z["item_ids"], z["bayes"], z["prior"]
        with np.load(self.root / "factors.npz", allow_pickle=False) as z:
            fids, fy = z["item_ids"], z["factors"]
        match = np.searchsorted(fids, ids)
        has = (match < len(fids)) & (fids[np.minimum(match, len(fids) - 1)] == ids)
        y = np.zeros((len(ids), self.cfg["als"]["rank"]), dtype=np.float64)
        y[has] = fy[match[has]]
        profiles = pd.read_parquet(self.root / "profiles.parquet")
        ns = self.cfg["ns"]
        rankings = np.full((len(profiles), len(ns), 2, 2), -1, dtype=np.int64)
        diagnostics = []
        digests = [hashlib.sha256() for _ in range(len(ns) * 2)]
        self.log("SCORE_START", users=len(profiles), items=len(ids))
        for u, row in enumerate(profiles.itertuples(index=False)):
            all_movies = np.asarray(row.profile_movie_ids, dtype=np.int64)
            all_indices = np.asarray(row.profile_rating_indices, dtype=np.int64)
            for k, n in enumerate(ns):
                # No target IDs/H/E enter the ranking function.
                positions = np.searchsorted(ids, all_movies[:n])
                available = np.ones(len(ids), dtype=bool); available[positions] = False
                pop = order(bayes, ids, available)
                rankings[u, k, 0] = ids[pop[:2]]
                pop_values = np.where(available, bayes, -np.inf)
                digests[k * 2].update(pop_values.astype("<f8").tobytes())
                als_active = content_active = False; factor_count = 0
                component_orders = []
                if n:
                    w = weights(all_indices[:n], prior)
                    if np.abs(w).sum() > 1e-12:
                        h = np.asarray(x[positions].T @ (w / np.abs(w).sum())).ravel()
                        if np.linalg.norm(h) > 1e-12:
                            scores_c = np.asarray(x @ h).ravel()
                            require(np.isfinite(scores_c).all(), "nonfinite content score")
                            component_orders.append(order(scores_c, ids, available)); content_active = True
                    use = has[positions]
                    factor_count = int(use.sum())
                    p = fold_in(y[positions[use]], (all_indices[:n][use] + 1) / 2, self.cfg["als"]["reg"])
                    if p is not None and np.linalg.norm(p) > 1e-12:
                        scores_a = y @ p
                        require(np.isfinite(scores_a).all(), "nonfinite ALS score")
                        component_orders.append(order(scores_a, ids, available & has)); als_active = True
                    chosen, hybrid_scores, fallback = choose_hybrid(component_orders, pop, ids)
                else:
                    chosen = pop[:2]; hybrid_scores = pop_values; fallback = "P0_NO_INPUT"
                require(len(chosen) == 2 and len(set(chosen)) == 2 and available[chosen].all(),
                        "supply/seen/duplicate failure")
                rankings[u, k, 1] = ids[chosen]
                digests[k * 2 + 1].update(np.where(available, hybrid_scores, -np.inf).astype("<f8").tobytes())
                diagnostics.append({"user_key": row.user_key, "n": n, "n_factor": factor_count,
                    "als_active": als_active, "content_active": content_active, "fallback": fallback,
                    "same_top2_as_p0": set(rankings[u, k, 0]) == set(rankings[u, k, 1])})
            if (u + 1) % 100 == 0:
                self.log("SCORE_PROGRESS", users=u + 1)
        require(np.array_equal(rankings[:, 0, 0], rankings[:, 0, 1]), "n0 identity failed")
        np.savez_compressed(self.root / "rankings.npz", user_keys=profiles.user_key.to_numpy(dtype=str),
                            ns=np.asarray(ns), movie_ids=rankings)
        pd.DataFrame(diagnostics).to_parquet(self.root / "score-diagnostics.parquet", index=False)
        self.seal("score", ["rankings.npz", "score-diagnostics.parquet"],
                  prepare_seal=pin(self.root / "prepare-seal.json"), train_seal=pin(self.root / "train-seal.json"),
                  full_score_sha256=[h.hexdigest() for h in digests], score_digest_order="n then P0/M0, sorted user_key",
                  labels_opened=False, output_stage="BASIC_RANK_TOP2")
        self.log("SCORE_SEALED")

    def evaluate(self):
        self.validate("prepare"); self.validate("train")
        score = self.validate("score")
        require(score["prepare_seal"] == pin(self.root / "prepare-seal.json")
                and score["train_seal"] == pin(self.root / "train-seal.json"), "score dependencies changed")
        if (self.root / "evaluate-seal.json").exists():
            self.validate("evaluate"); return
        self.fresh(["metrics.json", "user-metrics.parquet"])
        with np.load(self.root / "rankings.npz", allow_pickle=False) as z:
            keys, ns, ranked_movies = z["user_keys"], z["ns"], z["movie_ids"]
        members = pd.read_parquet(self.root / "evaluation-membership.parquet")
        profiles = pd.read_parquet(self.root / "profiles.parquet").set_index("user_key")
        expected = {(r.outer, r.user_key): set(map(int, r.target_movie_ids)) for r in members.itertuples(index=False)}
        source = self.source("labels", "evaluate")  # First label payload access, after all score seals.
        labels = pd.read_parquet(source, columns=["outer", "user_key", "target_movie_ids", "target_rating_indices",
                   "target_q", "full_history_count", "full_history_histogram"],
                   filters=[("user_key", "in", keys.tolist()), ("outer", "in", list(OUTERS))])
        require(not labels.duplicated(["outer", "user_key"]).any()
                and set(zip(labels.outer, labels.user_key)) == set(expected), "label/membership coverage")
        maps, raws, references = {}, {}, {}
        target_slots = 0
        for row in labels.itertuples(index=False):
            key = row.user_key
            movies = list(map(int, row.target_movie_ids)); indices = list(map(int, row.target_rating_indices))
            qs = list(map(float, row.target_q)); hist = tuple(map(int, row.full_history_histogram))
            require(len(movies) == len(set(movies)) == len(indices) == len(qs)
                    and set(movies) == expected[(row.outer, key)] and sum(hist) == row.full_history_count,
                    "label IDs/count/H mismatch")
            require(key not in references or references[key] == hist, "H differs across outer")
            references[key] = hist
            m, raw_map = maps.setdefault(key, {}), raws.setdefault(key, {})
            for movie, index, q in zip(movies, indices, qs, strict=True):
                fixed = q_from_hist(index, hist)
                require(np.isfinite(q) and abs(q - fixed) < 1e-12, "Q/H mismatch")
                require(movie not in m or (m[movie] == q and raw_map[movie] == (index + 1) / 2),
                        "conflicting duplicate user/movie label")
                m[movie] = q; raw_map[movie] = (index + 1) / 2
            target_slots += len(movies)
        for key in keys:
            require(key in maps and not set(maps[key]).intersection(profiles.loc[key, "profile_movie_ids"]),
                    "E/O overlap or missing user")
        values = np.zeros((len(keys), len(ns), 3, 2))
        rows = []
        aggregates = []
        for u, key in enumerate(keys):
            for k, n in enumerate(ns):
                p, m = ranked_movies[u, k]
                values[u, k] = paired_bounds(m.tolist(), p.tolist(), maps[key])
                for policy_index, policy in enumerate(self.cfg["policies"]):
                    movies = ranked_movies[u, k, policy_index].tolist()
                    qs = [maps[key].get(i) for i in movies]
                    bounds = metric_bounds(qs)
                    row = {"user_key": key, "n": int(n), "policy": policy, "movie_ids": movies,
                           "q": qs, "raw_ratings": [raws[key].get(i) for i in movies],
                           "label_count": sum(q is not None for q in qs)}
                    for j, metric in enumerate(["MEAN_Q", "MIN_Q", "HARM20"]):
                        row[metric + "_lower"], row[metric + "_upper"] = bounds[j].tolist()
                    rows.append(row)
        frame = pd.DataFrame(rows)
        for (n, policy), group in frame.groupby(["n", "policy"], sort=True):
            distribution = group.label_count.value_counts()
            aggregates.append({"n": int(n), "policy": policy, "users": len(group), "supplied_two": len(group),
                "users_by_label_count": {str(v): int(distribution.get(v, 0)) for v in (0, 1, 2)},
                "label_coverage": float(group.label_count.sum() / (2 * len(group))),
                "bounds": {metric: [float(group[metric + "_lower"].mean()), float(group[metric + "_upper"].mean())]
                           for metric in ("MEAN_Q", "MIN_Q", "HARM20")}})
        intervals = ci_bounds(values[:, 1:], self.cfg["bootstrap_repeats"], self.cfg["bootstrap_seed"],
                              self.cfg["alpha"], self.cfg["family_size"])
        differences = []
        for k, n in enumerate(ns[1:]):
            for j, metric in enumerate(["MEAN_Q", "MIN_Q", "HARM20"]):
                lo, hi = intervals[k, j].tolist()
                status = "UNDECIDED"
                if lo > 0:
                    status = "WORSE_DIRECTION" if metric == "HARM20" else "BETTER_DIRECTION"
                elif hi < 0:
                    status = "BETTER_DIRECTION" if metric == "HARM20" else "WORSE_DIRECTION"
                differences.append({"n": int(n), "metric": metric, "identification_bounds":
                    values[:, k + 1, j].mean(axis=0).tolist(), "simultaneous_exploratory_ci": [lo, hi], "status": status})
        diag = pd.read_parquet(self.root / "score-diagnostics.parquet")
        functionality = []
        for n, group in diag.groupby("n", sort=True):
            functionality.append({"n": int(n), "users": len(group), "als_active": int(group.als_active.sum()),
                "content_active": int(group.content_active.sum()), "same_top2_as_p0": int(group.same_top2_as_p0.sum()),
                "n_factor_min": int(group.n_factor.min()), "n_factor_mean": float(group.n_factor.mean()),
                "fallback": {str(k): int(v) for k, v in group.fallback.value_counts().items()}})
        result = {"experiment": "REC-EV-032", "claim_scope": "EXPLORATORY_REUSED_SELECTION_NOT_SERVICE_QUALITY",
                  "model": self.cfg["policies"][1], "users": len(keys), "items": self.cfg["expected_items"],
                  "label_outer_rows": len(labels), "target_slots_before_dedup": target_slots,
                  "unique_user_movie_labels": sum(map(len, maps.values())),
                  "labels_per_user_min": min(map(len, maps.values())), "labels_per_user_max": max(map(len, maps.values())),
                  "label_q_verified_from_fixed_H": True, "profile_target_overlap": 0, "n0_p0_m0_identical": True,
                  "metrics": aggregates, "paired_differences": differences, "functionality": functionality,
                  "product_adopted": False, "final_K_selected": False, "locked_or_reserve_opened": False}
        frame.to_parquet(self.root / "user-metrics.parquet", index=False)
        write_json(self.root / "metrics.json", result)
        self.seal("evaluate", ["metrics.json", "user-metrics.parquet"], score_seal=pin(self.root / "score-seal.json"),
                  label_source=self.cfg["inputs"]["labels"], label_rows=len(labels), new_independent_confirmation=False)
        self.log("EVALUATED", unique_labels=result["unique_user_movie_labels"])

    def close(self):
        try:
            self.guard()
        finally:
            self.done.set()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["prepare", "train", "score", "evaluate"])
    args = parser.parse_args()
    run = Run()
    try:
        getattr(run, args.phase)()
    except Exception as exc:
        failure_path = run.root / "failure.json"
        if not failure_path.exists() or read_json(failure_path).get("status") != "RESOURCE_LIMIT":
            write_json(failure_path, {"phase": args.phase, "error_type": type(exc).__name__,
                       "error": str(exc), "seconds": run.elapsed(), "fingerprint": run.identity})
        raise
    finally:
        run.close()


if __name__ == "__main__":
    main()
