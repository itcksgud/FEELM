"""Three predeclared user resplits; independent ALS/P0 training within each round."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import threading
import time
import zipfile
import rec_ev_032_basic as base
import rec_ev_032_conditional as conditional
import rec_ev_032_als_only as single
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import psutil

ROOT = base.ROOT
PLAN = ROOT / "docs/recommendation/experiments/rec-ev-032/user-resplits"
FILES = {"design": PLAN / "README.md", "config": PLAN / "config.json", "runner": Path(__file__).resolve(),
    "tests": ROOT / "scripts/tests/test_rec_ev_032_user_resplits.py", "base_runner": base.FILES["runner"],
    "conditional_runner": conditional.FILES["runner"], "als_runner": single.FILES["runner"]}
ROLE_OUTPUTS = ("splits.npz", "targets.parquet", "split-summary.json")
TRAIN_OUTPUTS = tuple(f"r{r}/{name}" for r in range(3) for name in ("training.parquet", "factors.npz", "bayes.npz"))
SCORE_OUTPUTS = tuple(f"r{r}/{name}" for r in range(3) for name in ("rankings.npz", "target-order.npz", "supply.parquet"))
OUTPUTS = (*ROLE_OUTPUTS, "role-seal.json", "profiles.parquet", "prepare-seal.json", *TRAIN_OUTPUTS,
    "train-seal.json", *SCORE_OUTPUTS, "score-seal.json", "evaluation-labels.parquet", "histograms.npz",
    "user-metrics.parquet", "metrics.json", "budget.json")


def fingerprint():
    return {key: base.sha(path) for key, path in FILES.items()}


def check_review():
    single.check_review()
    actual = fingerprint()
    review = base.read_json(PLAN / "review.json")
    base.require(review["status"] == "PASS" and review["fingerprint"] == actual, "resplit review absent or stale")
    return actual


def development_user(uid):
    return (0 < uid <= 200948 and base.bucket(uid, "feelm-rec-vnext-user-split-v1|", 100, True) <= 59
        and base.bucket(uid, "rec-ev-022a-user-role-v1|", 10000) <= 5999
        and base.bucket(uid, "rec-ev-028-user-phase-v1|", 10000) <= 7999)


def select_roles(development, eligible, seed, train_n, eval_n):
    development, eligible = set(map(int, development)), set(map(int, eligible))
    base.require(eligible <= development and eval_n <= len(eligible) and train_n <= len(development) - eval_n, "role capacity")
    def ordered(values, purpose):
        return sorted(values, key=lambda uid: (hashlib.sha256(f"rec032-resplit-{purpose}-v1|{seed}|{uid}".encode()).digest(), uid))
    evaluation = set(ordered(eligible, "eval")[:eval_n])
    training = ordered(development - evaluation, "train")[:train_n]
    return np.array(sorted(training), dtype=np.int64), np.array(sorted(evaluation, key=base.user_key), dtype=np.int64)


def scan_training_users(path, train_ids, eval_ids):
    actual, rows, overlap = set(), 0, 0
    for batch in pq.ParquetFile(path).iter_batches(batch_size=131072, columns=["user_id"]):
        base.require(batch.column(0).null_count == 0, "null training user")
        values = batch.column(0).to_numpy(zero_copy_only=False)
        actual.update(map(int, np.unique(values)))
        rows += len(values)
        overlap += int(np.isin(values, eval_ids).sum())
    base.require(actual == set(map(int, train_ids)) and overlap == 0, "actual training/evaluation role violation")
    return rows, len(actual), overlap


def across_splits(points, statuses, metric):
    if all(s == "BETTER_DIRECTION" for s in statuses):
        return "REPEATED_BENEFIT"
    if all(s == "WORSE_DIRECTION" for s in statuses):
        return "REPEATED_HARM"
    benefit = np.asarray(points) * (-1 if metric == "HARM20" else 1)
    if (benefit < 0).any() and (benefit > 0).any():
        return "SPLIT_SENSITIVE_DIRECTION"
    return "NOT_ESTABLISHED_ACROSS_ALL_SPLITS"


class Budget:
    def __init__(self, root, identity, config):
        self.root, self.identity, self.cfg = root, identity, config
        self.start, self.peak = time.monotonic(), 0
        self.done, self.lock = threading.Event(), threading.Lock()
        self.process = psutil.Process()
        self.thread = threading.Thread(target=self.watchdog, daemon=True)

    def elapsed(self):
        return time.monotonic() - self.start

    def sample(self):
        children = self.process.children(recursive=True)
        rss = self.process.memory_info().rss
        for child in children:
            try:
                rss += child.memory_info().rss
            except psutil.NoSuchProcess:
                pass
        self.peak = max(self.peak, rss)
        return children

    def exceeded(self):
        return self.elapsed() > self.cfg["max_seconds"] or self.peak > self.cfg["max_process_tree_bytes"]

    def save(self, failure=False):
        with self.lock:
            value = {"fingerprint": self.identity, "seconds": self.elapsed(), "peak_tree_rss_bytes": self.peak}
            base.write_json(self.root / "budget.json", value)
            if failure:
                base.write_json(self.root / "failure.json", {**value, "status": "RESOURCE_LIMIT"})

    def guard(self):
        self.sample()
        if self.exceeded():
            self.save(True)
            raise RuntimeError("resource limit")

    def watchdog(self):
        while not self.done.wait(2):
            children = self.sample()
            if self.exceeded():
                self.save(True)
                for child in reversed(children):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                os._exit(70)

    def close(self):
        self.done.set()
        if self.thread.is_alive():
            self.thread.join()
        self.sample()
        self.save()


class Run:
    def __init__(self):
        self.identity, self.cfg = check_review(), base.read_json(FILES["config"])
        c = self.cfg
        base.require(c["split_seeds"] == [20260908, 20260909, 20260910] and c["ns"] == [0, 5, 10, 30]
            and c["policies"] == ["P0", "A0_RAW_ALS_ONLY"] and c["top_n"] == 2
            and (c["train_users_per_round"], c["evaluation_users_per_round"]) == (46376, 2180)
            and (c["bootstrap_repeats"], c["bootstrap_seed"], c["alpha"], c["family_size"]) == (20000, 20260908, .05, 27)
            and c["popularity_prior"] == 50 and c["direction_threshold"] == 0 and c["practical_adoption_margin"] is None,
            "fixed analysis drift")
        old = base.read_json(base.FILES["config"])
        base.require(all(c["als"][k] == v for k, v in old["als"].items() if k != "train_scope"), "ALS configuration changed")
        self.root = ROOT / c["output_root"]
        base.require(self.root.resolve() == (ROOT / "outputs/recommendation-evidence/rec-ev-032/user-resplits").resolve(), "output path")
        self.paths, self.budget = {}, None

    def guard(self):
        base.require(check_review() == self.identity, "review/code changed")
        if self.budget:
            self.budget.guard()

    def log(self, phase, **values):
        self.guard()
        print({"phase": phase, "seconds": round(self.budget.elapsed(), 2), **values}, flush=True)

    def sources(self):
        self.guard()
        for name, spec in self.cfg["inputs"].items():
            p = Path(spec["path"])
            if not p.is_absolute():
                p = ROOT / p
            if name == "archive":
                base.require(p.resolve() == Path(base.read_json(base.FILES["config"])["inputs"]["archive"]["path"]).resolve(), "archive outside allowlist")
            else:
                base.require(p.resolve().is_relative_to(ROOT.resolve()), "source outside research repo")
            base.require(base.pin(p) == {"bytes": spec["bytes"], "sha256": spec["sha256"]}, "source drift: " + name)
            self.paths[name] = p
        for cohort in ("selection", "replication"):
            seal = base.read_json(self.paths[cohort + "_label_seal"])
            base.require(seal["status"] == f"OPENED_{cohort.upper()}_TARGET_LABELS_ONCE_AFTER_REQUIRED_SEALS"
                and seal["labels"] == self.cfg["inputs"][cohort + "_labels"], "label provenance drift")
        base.require(base.read_json(self.paths["replication_audit"])["status"] == "REC_EV_029_REPLICATION_RESULT_AUDIT_PASS", "prior replication not audited")

    def seal(self, name, outputs, **fields):
        self.guard()
        base.write_json(self.root / name, {"status": "COMPLETE", "fingerprint": self.identity,
            "outputs": {n: base.pin(self.root / n) for n in outputs}, **fields})

    def validate_seal(self, name, outputs, dependencies=None):
        seal = base.read_json(self.root / name)
        base.require(seal["status"] == "COMPLETE" and seal["fingerprint"] == self.identity
            and set(seal["outputs"]) == set(outputs), "seal drift: " + name)
        for key, expected in (dependencies or {}).items():
            base.require(seal[key] == expected, "seal dependency drift: " + key)
        for output, expected in seal["outputs"].items():
            base.require(base.pin(self.root / output) == expected, "sealed output drift: " + output)
        return seal

    def prepare(self):
        c = self.cfg
        with np.load(self.paths["universe"], allow_pickle=False) as z:
            self.ids = z["item_ids"].astype(np.int64)
        base.require(len(self.ids) == c["expected_items"] and np.all(np.diff(self.ids) > 0), "catalogue drift")
        pool = pd.read_parquet(self.paths["profiles"], columns=["cohort", "outer", "user_key", "profile_movie_ids"], filters=[("outer", "==", "R0")])
        base.require(pool.groupby("cohort").user_key.nunique().to_dict() == c["expected_evaluation_cohorts"]
            and len(pool) == c["expected_evaluation_pool_users"] and not pool.user_key.duplicated().any(), "evaluation pool changed")
        development = np.array([u for u in range(1, c["max_user_id"] + 1) if development_user(u)], dtype=np.int64)
        base.require(len(development) == c["expected_development_users"], "development population drift")
        key_to_id = {base.user_key(int(uid)): int(uid) for uid in development}
        base.require(len(key_to_id) == len(development) and set(pool.user_key) <= set(key_to_id), "evaluation role mapping")
        roles = [select_roles(development, [key_to_id[k] for k in pool.user_key], seed,
            c["train_users_per_round"], c["evaluation_users_per_round"]) for seed in c["split_seeds"]]
        self.train_ids, self.eval_ids = np.stack([v[0] for v in roles]), np.stack([v[1] for v in roles])
        self.eval_keys = np.array([[base.user_key(int(uid)) for uid in ids] for ids in self.eval_ids])
        self.keys = np.array(sorted(set(self.eval_keys.ravel())))
        self.pool = pool.set_index("user_key").loc[self.keys].reset_index()
        members = pd.read_parquet(self.paths["membership"], columns=["cohort", "outer", "user_key", "profile_movie_ids", "target_movie_ids"],
            filters=[("user_key", "in", self.keys.tolist())])
        base.require(not members.duplicated(["cohort", "outer", "user_key"]).any()
            and set(members.user_key) == set(self.keys) and set(members.outer) <= set(base.OUTERS), "membership invalid")
        self.expected, self.targets = {}, {key: set() for key in self.keys}
        cohorts = self.pool.set_index("user_key").cohort.to_dict()
        profile_ids = self.pool.set_index("user_key").profile_movie_ids.to_dict()
        for row in members.itertuples(index=False):
            target = list(map(int, row.target_movie_ids))
            base.require(row.cohort == cohorts[row.user_key] and len(target) == len(set(target)), "target/cohort mismatch")
            self.expected[(row.cohort, row.outer, row.user_key)] = set(target)
            self.targets[row.user_key].update(target)
            if row.outer == "R0":
                base.require(np.array_equal(row.profile_movie_ids, profile_ids[row.user_key]), "R0 profile mismatch")
        catalogue = set(self.ids)
        for key in self.keys:
            movies = list(map(int, profile_ids[key])); target = self.targets[key]
            base.require(len(movies) == len(set(movies)) == 30 and set(movies) <= catalogue and target <= catalogue
                and len(target) >= 2 and not set(movies).intersection(target), "E/O/catalogue failure")
            self.targets[key] = np.array(sorted(target), dtype=np.int64)
        original = set(pd.read_parquet(self.paths["original_profiles"], columns=["user_key"]).user_key)
        with np.load(self.paths["original_prepared"], allow_pickle=False) as z:
            original_train = set(map(int, z["training_user_ids"]))
        overlap = []
        for a in range(3):
            for b in range(a + 1, 3):
                train_common = len(set(self.train_ids[a]) & set(self.train_ids[b]))
                eval_common = len(set(self.eval_ids[a]) & set(self.eval_ids[b]))
                overlap.append({"rounds": [a, b], "train_overlap_users": train_common,
                    "train_overlap_fraction": train_common / c["train_users_per_round"],
                    "eval_overlap_users": eval_common, "eval_overlap_fraction": eval_common / c["evaluation_users_per_round"]})
        rounds = [{"round": r, "seed": c["split_seeds"][r], "train_users": len(self.train_ids[r]), "eval_users": len(self.eval_ids[r]),
            "train_eval_overlap": len(set(self.train_ids[r]) & set(self.eval_ids[r])),
            "original_train_overlap": len(set(self.train_ids[r]) & original_train), "original_eval_overlap": len(set(self.eval_keys[r]) & original),
            "eval_source_cohorts": pd.Series([cohorts[k] for k in self.eval_keys[r]]).value_counts().to_dict(),
            "E_pairs": sum(len(self.targets[k]) for k in self.eval_keys[r]),
            "min_E": min(len(self.targets[k]) for k in self.eval_keys[r]), "max_E": max(len(self.targets[k]) for k in self.eval_keys[r])} for r in range(3)]
        base.require(all(row["train_eval_overlap"] == 0 for row in rounds), "round role overlap")
        np.savez_compressed(self.root / "splits.npz", development_user_ids=development, training_user_ids=self.train_ids,
            evaluation_user_ids=self.eval_ids, evaluation_user_keys=self.eval_keys, split_seeds=np.array(c["split_seeds"]))
        pd.DataFrame({"user_key": self.keys, "cohort": [cohorts[k] for k in self.keys], "movie_ids": [self.targets[k] for k in self.keys]}).to_parquet(self.root / "targets.parquet", index=False)
        base.write_json(self.root / "split-summary.json", {"development_users": len(development), "evaluation_pool_users": len(pool),
            "unique_evaluation_users": len(self.keys), "rounds": rounds, "between_round_overlaps": overlap,
            "all_E_O30_overlap": 0, "new_confirmation": False})
        self.seal("role-seal.json", ROLE_OUTPUTS, source_membership=base.pin(self.paths["membership"]),
            source_profiles=base.pin(self.paths["profiles"]), rating_values_decoded=False)
        self.validate_seal("role-seal.json", ROLE_OUTPUTS)
        profiles = pd.read_parquet(self.paths["profiles"], columns=["cohort", "outer", "user_key", "profile_movie_ids", "profile_rating_indices"],
            filters=[("outer", "==", "R0"), ("user_key", "in", self.keys.tolist())]).set_index("user_key").loc[self.keys].reset_index()
        for row in profiles.itertuples(index=False):
            values = np.asarray(row.profile_rating_indices)
            base.require(np.array_equal(row.profile_movie_ids, profile_ids[row.user_key]) and row.cohort == cohorts[row.user_key]
                and values.shape == (30,) and np.all((values >= 0) & (values <= 9) & (values == values.astype(np.int64))), "input profile changed")
        self.profiles = profiles.set_index("user_key")
        profiles.to_parquet(self.root / "profiles.parquet", index=False)
        self.seal("prepare-seal.json", ["profiles.parquet"], role_seal=base.pin(self.root / "role-seal.json"), sources=self.cfg["inputs"])
        self.log("ROLES_AND_INPUTS_SEALED", unique_evaluation_users=len(self.keys))

    def extract(self, r):
        folder = self.root / f"r{r}"; folder.mkdir()
        mask = np.zeros(self.cfg["max_user_id"] + 1, dtype=bool); mask[self.train_ids[r]] = True
        base.require(not mask[self.eval_ids[r]].any(), "round mask leakage")
        counts = np.zeros(self.cfg["max_movie_id"] + 1, dtype=np.int64)
        sums = np.zeros(len(counts), dtype=np.float64)
        active = np.zeros(len(mask), dtype=bool)
        schema = pa.schema([("user_id", pa.int32()), ("movie_id", pa.int32()), ("rating", pa.float32())])
        users, movies, ratings = [], [], []
        rows = parsed = 0
        def flush(writer):
            writer.write_table(pa.Table.from_arrays([pa.array(users, type=pa.int32()), pa.array(movies, type=pa.int32()), pa.array(ratings, type=pa.float32())], schema=schema))
            users.clear(); movies.clear(); ratings.clear()
        with pq.ParquetWriter(folder / "training.parquet", schema) as writer:
            with zipfile.ZipFile(self.paths["archive"]) as archive, archive.open("ml-32m/ratings.csv") as stream:
                base.require(stream.readline().rstrip(b"\r\n") == b"userId,movieId,rating,timestamp", "archive schema")
                for line in stream:
                    rows += 1
                    value = base.filtered_rating(line, mask, self.cfg["max_movie_id"])
                    if value is not None:
                        uid, movie, index = value; raw = (index + 1) / 2
                        users.append(uid); movies.append(movie); ratings.append(raw)
                        active[uid] = True; counts[movie] += 1; sums[movie] += raw; parsed += 1
                        if len(users) >= 100000:
                            flush(writer)
                    if rows % 8000000 == 0:
                        self.log("TRAIN_EXTRACT", round=r, source_rows=rows, selected_rows=parsed)
                if users:
                    flush(writer)
        base.require(rows == self.cfg["expected_raw_rows"] and np.array_equal(np.flatnonzero(active), self.train_ids[r]), "training extraction mismatch")
        checked_rows, checked_users, overlap_rows = scan_training_users(folder / "training.parquet", self.train_ids[r], self.eval_ids[r])
        base.require(checked_rows == parsed == int(counts.sum()), "actual training rows mismatch")
        mu = float(sums.sum() / counts.sum())
        bayes = (sums[self.ids] + 50 * mu) / (counts[self.ids] + 50)
        np.savez_compressed(folder / "bayes.npz", item_ids=self.ids, bayes=bayes, counts=counts[self.ids], sums=sums[self.ids],
            global_mean=np.array(mu), global_rating_count=np.array(parsed), global_rating_sum=np.array(sums.sum()), training_user_ids=self.train_ids[r])
        return {"round": r, "source_rows": rows, "training_rows": parsed, "training_users": checked_users,
            "evaluation_users_in_training_rows": overlap_rows, "excluded_row_rating_values_parsed": 0, "timestamps_parsed": 0, "global_mean": mu}

    def train(self):
        self.validate_seal("prepare-seal.json", ["profiles.parquet"], {"role_seal": base.pin(self.root / "role-seal.json")})
        stats = [self.extract(r) for r in range(3)]
        from pyspark.ml.recommendation import ALS
        from pyspark.sql import SparkSession
        c = self.cfg["als"]
        spark = (SparkSession.builder.master(c["master"]).appName("feelm-rec032-user-resplits")
            .config("spark.driver.memory", c["driver_memory"]).config("spark.sql.shuffle.partitions", str(c["shuffle_partitions"]))
            .config("spark.ui.enabled", "false").config("spark.ui.showConsoleProgress", "false").getOrCreate())
        try:
            spark.sparkContext.setLogLevel("ERROR")
            for r in range(3):
                self.log("ALS_TRAIN_START", round=r, training_rows=stats[r]["training_rows"])
                frame = spark.read.parquet(str(self.root / f"r{r}/training.parquet"))
                base.require(frame.count() == stats[r]["training_rows"], "Spark training count mismatch")
                model = ALS(rank=c["rank"], regParam=c["reg"], maxIter=c["iterations"], seed=c["seed"],
                    userCol="user_id", itemCol="movie_id", ratingCol="rating", implicitPrefs=False, nonnegative=False,
                    coldStartStrategy="nan", numUserBlocks=c["num_user_blocks"], numItemBlocks=c["num_item_blocks"]).fit(frame)
                factors = model.itemFactors.toPandas().sort_values("id")
                ids, y = factors.id.to_numpy(dtype=np.int64), np.asarray(factors.features.tolist(), dtype=np.float64)
                base.require(np.all(np.diff(ids) > 0) and y.shape == (len(ids), c["rank"]) and np.isfinite(y).all(), "invalid learned factor")
                np.savez_compressed(self.root / f"r{r}/factors.npz", item_ids=ids, factors=y)
                stats[r].update(factor_items=len(ids), spark_version=spark.version)
                del model, frame, factors
                spark.catalog.clearCache()
                self.log("ALS_TRAIN_COMPLETE", round=r, factor_items=len(ids))
        finally:
            spark.stop()
        self.seal("train-seal.json", TRAIN_OUTPUTS, prepare_seal=base.pin(self.root / "prepare-seal.json"),
            role_seal=base.pin(self.root / "role-seal.json"), rounds=stats, training_runs=3,
            role_boundary="PER_ROUND_NOT_GLOBAL_ACROSS_RESPLITS")

    def score(self):
        self.validate_seal("train-seal.json", TRAIN_OUTPUTS, {"prepare_seal": base.pin(self.root / "prepare-seal.json"), "role_seal": base.pin(self.root / "role-seal.json")})
        round_metadata = []
        for r in range(3):
            keys = self.eval_keys[r]
            with np.load(self.root / f"r{r}/bayes.npz", allow_pickle=False) as z:
                base.require(np.array_equal(z["item_ids"], self.ids), "P0 catalogue mismatch")
                bayes = z["bayes"]
            with np.load(self.root / f"r{r}/factors.npz", allow_pickle=False) as z:
                fids, fy = z["item_ids"], z["factors"]
            match = np.searchsorted(fids, self.ids)
            has = (match < len(fids)) & (fids[np.minimum(match, len(fids) - 1)] == self.ids)
            y = np.zeros((len(self.ids), self.cfg["als"]["rank"]), dtype=np.float64); y[has] = fy[match[has]]
            offsets = np.concatenate(([0], np.cumsum([len(self.targets[k]) for k in keys]))).astype(np.int64)
            target_ids = np.concatenate([self.targets[k] for k in keys])
            ranked = np.full((len(keys), 4, 2, 2), -1, dtype=np.int64)
            global_ranks = np.zeros((len(target_ids), 4, 2), dtype=np.int64)
            global_scores = np.full(global_ranks.shape, -np.inf, dtype=np.float64)
            score_hashes, order_hashes = [hashlib.sha256() for _ in range(8)], [hashlib.sha256() for _ in range(8)]
            supply = []
            for u, key in enumerate(keys):
                row = self.profiles.loc[key]
                movies, indices = np.asarray(row.profile_movie_ids), np.asarray(row.profile_rating_indices)
                target = self.targets[key]; positions = np.searchsorted(self.ids, target); section = slice(offsets[u], offsets[u + 1])
                for k, n in enumerate(self.cfg["ns"]):
                    aorder, avalue, diag = single.als_order(self.ids, bayes, y, has, movies, indices, n, self.cfg["als"]["reg"])
                    available = np.ones(len(self.ids), dtype=bool); available[np.searchsorted(self.ids, movies[:n])] = False
                    pvalues = np.where(available, bayes, -np.inf)
                    porder = base.order(pvalues, self.ids)
                    for p, (order, values) in enumerate(((porder, pvalues), (aorder, avalue))):
                        chosen, ranks = conditional.projected_order(order, positions, len(self.ids))
                        ranked[u, k, p, :len(chosen)] = target[chosen]
                        global_ranks[section, k, p], global_scores[section, k, p] = ranks, values[positions]
                        score_hashes[k * 2 + p].update(values.astype("<f8").tobytes())
                        order_hashes[k * 2 + p].update(np.array([len(order)], dtype="<i8").tobytes()); order_hashes[k * 2 + p].update(order.astype("<i8").tobytes())
                        supply.append({"round": r, "user_key": key, "n": n, "policy": self.cfg["policies"][p],
                            "targets": len(target), "factor_targets": int(has[positions].sum()), "supported_targets": int((ranks > 0).sum()),
                            "supplied": len(chosen), "factorless_selected": int((~has[positions[chosen]]).sum()),
                            "n_factor": diag["n_factor"], "fallback": diag["fallback"] if p else "P0_BASELINE"})
                if (u + 1) % 400 == 0:
                    self.log("SCORE_PROGRESS", round=r, users=u + 1)
            supply_frame = pd.DataFrame(supply)
            supply_frame.to_parquet(self.root / f"r{r}/supply.parquet", index=False)
            conditional.check_supply(supply_frame.supplied.to_numpy())
            base.require(np.array_equal(ranked[:, 0, 0], ranked[:, 0, 1]), "n0 identity mismatch")
            np.savez_compressed(self.root / f"r{r}/rankings.npz", user_keys=keys, ns=np.array(self.cfg["ns"]), movie_ids=ranked)
            np.savez_compressed(self.root / f"r{r}/target-order.npz", user_keys=keys, offsets=offsets, movie_ids=target_ids,
                global_ranks=global_ranks, global_scores=global_scores, factor_supported=has[np.searchsorted(self.ids, target_ids)])
            summaries = [{"n": int(n), "policy": policy, "users": len(group), "E_pairs": int(group.targets.sum()),
                "factor_E_pairs": int(group.factor_targets.sum()), "supported_E_pairs": int(group.supported_targets.sum()),
                "slots": int(group.supplied.sum()), "factorless_selected_slots": int(group.factorless_selected.sum()),
                "min_input_factors": int(group.n_factor.min()), "max_input_factors": int(group.n_factor.max()),
                "fallback_counts": {str(a): int(b) for a, b in group.fallback.value_counts().items()}}
                for (n, policy), group in supply_frame.groupby(["n", "policy"], sort=True)]
            round_metadata.append({"round": r, "catalogue_factor_items": int(has.sum()), "full_score_sha256": [h.hexdigest() for h in score_hashes],
                "full_order_sha256": [h.hexdigest() for h in order_hashes], "supply": summaries})
        self.seal("score-seal.json", SCORE_OUTPUTS, train_seal=base.pin(self.root / "train-seal.json"),
            prepare_seal=base.pin(self.root / "prepare-seal.json"), rounds=round_metadata,
            evaluation_label_files_decoded=False, cross_round_training_role_switches_allowed=True)

    def evaluate(self):
        self.validate_seal("score-seal.json", SCORE_OUTPUTS, {"train_seal": base.pin(self.root / "train-seal.json"), "prepare_seal": base.pin(self.root / "prepare-seal.json")})
        maps, hists, actual_members = {key: {} for key in self.keys}, {}, set()
        for cohort in ("SELECTION", "REPLICATION"):
            keys = self.pool[self.pool.cohort.eq(cohort)].user_key.tolist()
            frame = pd.read_parquet(self.paths[cohort.lower() + "_labels"], columns=["outer", "user_key", "target_movie_ids", "target_rating_indices", "target_q", "full_history_count", "full_history_histogram"],
                filters=[("user_key", "in", keys), ("outer", "in", list(base.OUTERS))])
            for row in frame.itertuples(index=False):
                identity = (cohort, row.outer, row.user_key)
                base.require(identity in self.expected and identity not in actual_members, "label membership mismatch")
                actual_members.add(identity)
                hist = tuple(map(int, row.full_history_histogram))
                base.require(len(hist) == 10 and min(hist) >= 0 and sum(hist) == row.full_history_count and sum(hist) > 0
                    and (row.user_key not in hists or hists[row.user_key] == hist), "fixed H drift")
                hists[row.user_key] = hist
                movies, indices, qs = row.target_movie_ids, row.target_rating_indices, row.target_q
                base.require(len(movies) == len(set(movies)) == len(indices) == len(qs) and set(movies) == self.expected[identity], "label E drift")
                for movie, index, q in zip(movies, indices, qs, strict=True):
                    base.require(np.isfinite(index) and int(index) == index and 0 <= index <= 9, "label grid")
                    raw, fixed = (int(index) + 1) / 2, base.q_from_hist(int(index), hist)
                    base.require(np.isfinite(q) and abs(q - fixed) < 1e-12, "fixed Q drift")
                    old = maps[row.user_key].get(int(movie)); value = (raw, fixed)
                    base.require(old is None or old == value, "duplicate label disagreement")
                    maps[row.user_key][int(movie)] = value
        base.require(actual_members == set(self.expected) and all(set(maps[k]) == set(self.targets[k]) for k in self.keys), "full label coverage failure")
        pd.DataFrame([{"user_key": key, "movie_id": movie, "rating_raw": raw, "q": q} for key in self.keys
            for movie, (raw, q) in sorted(maps[key].items())]).to_parquet(self.root / "evaluation-labels.parquet", index=False)
        np.savez_compressed(self.root / "histograms.npz", user_keys=self.keys, histograms=np.array([hists[k] for k in self.keys], dtype=np.int64))
        rows, comparisons = [], []
        for r in range(3):
            with np.load(self.root / f"r{r}/rankings.npz", allow_pickle=False) as z:
                keys, ranked = z["user_keys"], z["movie_ids"]
                base.require(np.array_equal(keys, self.eval_keys[r]), "scored cohort drift")
            differences = np.zeros((len(keys), 3, 3, 2), dtype=np.float64)
            for u, key in enumerate(keys):
                for k, n in enumerate(self.cfg["ns"]):
                    raws = []
                    for p, policy in enumerate(self.cfg["policies"]):
                        movies = ranked[u, k, p].tolist()
                        base.require(len(set(movies)) == 2 and all(m in maps[key] for m in movies), "unscorable top2")
                        raw, qs = zip(*(maps[key][m] for m in movies), strict=True); raws.append(raw)
                        numerator, denominator = conditional.metric_parts(raw, hists[key])
                        rows.append({"round": r, "user_key": key, "n": n, "policy": policy, "movie_ids": movies,
                            "raw_ratings": list(raw), "q": list(qs), **dict(zip(conditional.METRICS, (numerator / denominator).tolist()))})
                    if k:
                        differences[u, k - 1] = conditional.observed_difference(raws[1], raws[0], hists[key])
            self.guard()
            intervals = base.ci_bounds(differences, self.cfg["bootstrap_repeats"], self.cfg["bootstrap_seed"], self.cfg["alpha"], self.cfg["family_size"])
            for k, n in enumerate(self.cfg["ns"][1:]):
                for j, metric in enumerate(conditional.METRICS):
                    lo, hi = intervals[k, j].tolist()
                    comparisons.append({"round": r, "n": n, "metric": metric, "difference_ALS_minus_P0": float(differences[:, k, j, 0].mean()),
                        "simultaneous_exploratory_ci": [lo, hi], "ci_width": hi - lo, "status": conditional.direction(metric, lo, hi)})
            self.log("ROUND_EVALUATED", round=r)
        frame = pd.DataFrame(rows)
        frame.to_parquet(self.root / "user-metrics.parquet", index=False)
        aggregate = [{"round": int(r), "n": int(n), "policy": policy, "users": len(g), "slots": 2 * len(g),
            "metrics": {m: float(g[m].mean()) for m in conditional.METRICS}} for (r, n, policy), g in frame.groupby(["round", "n", "policy"], sort=True)]
        repeated = []
        for n in self.cfg["ns"][1:]:
            for metric in conditional.METRICS:
                selected = [row for row in comparisons if row["n"] == n and row["metric"] == metric]
                points, statuses = [row["difference_ALS_minus_P0"] for row in selected], [row["status"] for row in selected]
                repeated.append({"n": n, "metric": metric, "points": points, "round_statuses": statuses,
                    "status": across_splits(points, statuses, metric)})
        base.write_json(self.root / "metrics.json", {"experiment": self.cfg["experiment"], "claim_scope": "THREE_FIXED_DEVELOPMENT_USER_RESPLITS_OBSERVED_E_ONLY",
            "metrics": aggregate, "paired_differences": comparisons, "across_splits": repeated,
            "n30_all_three_metrics_repeated_benefit": all(row["status"] == "REPEATED_BENEFIT" for row in repeated if row["n"] == 30),
            "unique_evaluation_users": len(self.keys), "round_user_occurrences": 3 * self.cfg["evaluation_users_per_round"],
            "pooled_independent_user_claim": False, "training_runs": 3, "new_confirmation": False,
            "final_K_selected": False, "product_adopted": False, "learning_convergence_verified": False})

    def run(self):
        self.sources()
        complete = self.root / "completion-seal.json"
        if complete.exists():
            self.validate_seal("completion-seal.json", OUTPUTS, {"sources": self.cfg["inputs"]})
            self.validate_seal("role-seal.json", ROLE_OUTPUTS, {"source_membership": base.pin(self.paths["membership"]), "source_profiles": base.pin(self.paths["profiles"])})
            self.validate_seal("prepare-seal.json", ["profiles.parquet"], {"role_seal": base.pin(self.root / "role-seal.json"), "sources": self.cfg["inputs"]})
            self.validate_seal("train-seal.json", TRAIN_OUTPUTS, {"prepare_seal": base.pin(self.root / "prepare-seal.json"), "role_seal": base.pin(self.root / "role-seal.json")})
            self.validate_seal("score-seal.json", SCORE_OUTPUTS, {"train_seal": base.pin(self.root / "train-seal.json"), "prepare_seal": base.pin(self.root / "prepare-seal.json")})
            print("VERIFIED_EXISTING_COMPLETION_NO_RETRAINING_OR_RESCORING")
            return
        base.require(not self.root.exists() or not any(self.root.iterdir()), "partial execution exists; preserve without retry")
        self.root.mkdir(parents=True, exist_ok=True)
        self.budget = Budget(self.root, self.identity, self.cfg); self.budget.save(); self.budget.thread.start()
        try:
            self.prepare(); self.train(); self.score(); self.evaluate()
            self.sources(); self.guard(); self.budget.close(); self.guard()
            self.seal("completion-seal.json", OUTPUTS, sources=self.cfg["inputs"], seconds=self.budget.elapsed(),
                peak_tree_rss_bytes=self.budget.peak, training_runs=3, previous_artifacts_preserved=True)
            self.log("USER_RESPLITS_COMPLETE")
        except Exception as exc:
            self.budget.close()
            if not (self.root / "failure.json").exists():
                base.write_json(self.root / "failure.json", {"status": "FAILED", "fingerprint": self.identity,
                    "error_type": type(exc).__name__, "error": str(exc)})
            raise


if __name__ == "__main__":
    Run().run()
