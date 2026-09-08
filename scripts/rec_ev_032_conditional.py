"""REC032 conditional diagnostic: preserve full scores, then filter fixed E."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import threading
import time
import rec_ev_032_basic as base  # Sets BLAS thread limits before numpy.
import numpy as np
import pandas as pd
import psutil
from scipy import sparse

ROOT = base.ROOT
PLAN = ROOT / "docs/recommendation/experiments/rec-ev-032/conditional-ranking"
FILES = {"design": PLAN / "README.md", "config": PLAN / "config.json",
         "runner": Path(__file__).resolve(), "tests": ROOT / "scripts/tests/test_rec_ev_032_conditional.py",
         "base_runner": ROOT / "scripts/rec_ev_032_basic.py"}
METRICS = ("MEAN_Q", "MIN_Q", "HARM20")
SCORE_OUTPUTS = ("rankings.npz", "target-order.npz", "supply.parquet", "supply-summary.json")
OUTPUTS = ("targets.parquet", "request-seal.json", *SCORE_OUTPUTS, "score-seal.json",
           "evaluation-labels.parquet", "histograms.npz", "user-metrics.parquet", "metrics.json", "budget.json")


def fingerprint():
    return {k: base.sha(p) for k, p in FILES.items()}


def check_review():
    base.check_review()
    actual = fingerprint()
    review = base.read_json(PLAN / "review.json")
    base.require(review["status"] == "PASS" and review["fingerprint"] == actual, "conditional review absent or stale")
    return actual


def original_order(pop, scores, chosen, fallback, ids, available):
    if fallback in ("P0_NO_INPUT", "P0_NO_COMPONENT"):
        return pop.copy()
    ranked = base.order(scores, ids, available & (scores > 0))
    if fallback == "P0_SUPPLY_TOPUP":
        base.require(len(ranked) < 2, "unexpected global topup")
        ranked = np.asarray(list(ranked) + [int(i) for i in chosen if i not in set(ranked)], dtype=np.int64)
    else:
        base.require(fallback == "NONE", "unknown global fallback")
    base.require(np.array_equal(ranked[:2], chosen), "full order does not reproduce top2")
    return ranked


def score_one(ids, bayes, prior, x, y, has, movies, indices, n, reg):
    # E and evaluation labels are deliberately absent from this function.
    positions = np.searchsorted(ids, movies[:n])
    available = np.ones(len(ids), dtype=bool)
    available[positions] = False
    pop = base.order(bayes, ids, available)
    pop_values = np.where(available, bayes, -np.inf)
    als_active = content_active = False
    factor_count = 0
    component_orders = []
    if n:
        w = base.weights(indices[:n], prior)
        if np.abs(w).sum() > 1e-12:
            h = np.asarray(x[positions].T @ (w / np.abs(w).sum())).ravel()
            if np.linalg.norm(h) > 1e-12:
                scores_c = np.asarray(x @ h).ravel()
                base.require(np.isfinite(scores_c).all(), "nonfinite content score")
                component_orders.append(base.order(scores_c, ids, available))
                content_active = True
        use = has[positions]
        factor_count = int(use.sum())
        p = base.fold_in(y[positions[use]], (indices[:n][use] + 1) / 2, reg)
        if p is not None and np.linalg.norm(p) > 1e-12:
            scores_a = y @ p
            base.require(np.isfinite(scores_a).all(), "nonfinite ALS score")
            component_orders.append(base.order(scores_a, ids, available & has))
            als_active = True
        chosen, hybrid_scores, fallback = base.choose_hybrid(component_orders, pop, ids)
    else:
        chosen = pop[:2]
        hybrid_scores = pop_values
        fallback = "P0_NO_INPUT"
    base.require(len(chosen) == 2 and len(set(chosen)) == 2 and available[chosen].all(), "original supply failure")
    full_m = original_order(pop, hybrid_scores, chosen, fallback, ids, available)
    diag = {"n_factor": factor_count, "als_active": als_active, "content_active": content_active,
            "fallback": fallback, "same_top2_as_p0": set(ids[pop[:2]]) == set(ids[chosen])}
    return (pop, full_m), (pop_values, np.where(available, hybrid_scores, -np.inf)), diag


def projected_order(full_order, target_positions, size):
    inverse = np.zeros(size, dtype=np.int64)  # 0 means not supported by the original order.
    inverse[full_order] = np.arange(1, len(full_order) + 1, dtype=np.int64)
    ranks = inverse[target_positions]
    valid = np.flatnonzero(ranks > 0)
    ordered = valid[np.argsort(ranks[valid], kind="stable")]
    return ordered[:2], ranks


def check_supply(counts):
    base.require(np.all(counts == 2), "conditional supply failure; retain all users, do not score")


def direction(metric, lo, hi):
    if lo > 0:
        return "WORSE_DIRECTION" if metric == "HARM20" else "BETTER_DIRECTION"
    if hi < 0:
        return "BETTER_DIRECTION" if metric == "HARM20" else "WORSE_DIRECTION"
    return "UNDECIDED"


def metric_parts(raw_ratings, histogram):
    # Fixed-H Q is rational. Subtract integer numerators before division so
    # mathematical ties never become tiny signed floating-point improvements.
    base.require(len(raw_ratings) == 2, "two observed ratings required")
    hist = np.asarray(histogram, dtype=np.int64)
    base.require(hist.shape == (10,) and (hist >= 0).all() and hist.sum() > 0, "fixed H invalid")
    indices = [base.rating_index(raw) for raw in raw_ratings]
    qnums = [2 * int(hist[:i].sum()) + int(hist[i]) for i in indices]
    total = int(hist.sum())
    numerators = np.array([sum(qnums), min(qnums), int(any(5 * q <= 2 * total for q in qnums))], dtype=np.int64)
    denominators = np.array([4 * total, 2 * total, 1], dtype=np.float64)
    return numerators, denominators


def observed_difference(raw_m, raw_p, histogram):
    m, denominator = metric_parts(raw_m, histogram)
    p, other = metric_parts(raw_p, histogram)
    base.require(np.array_equal(denominator, other), "paired H differs")
    point = (m - p) / denominator
    return np.column_stack([point, point])


def verify_original_scores(actual, expected, actual_top2, old_top2):
    base.require(actual == expected, "original full score digest mismatch")
    base.require(np.array_equal(actual_top2, old_top2), "original full-catalog top2 mismatch")


class Budget:
    def __init__(self, root, identity, config):
        self.root, self.identity, self.cfg = root, identity, config
        self.start, self.peak = time.monotonic(), 0
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.process = psutil.Process()
        self.thread = threading.Thread(target=self.watchdog, daemon=True)

    def elapsed(self):
        return time.monotonic() - self.start

    def save(self, failure=None):
        with self.lock:
            value = {"fingerprint": self.identity, "seconds": self.elapsed(), "peak_rss_bytes": self.peak}
            base.write_json(self.root / "budget.json", value)
            if failure:
                base.write_json(self.root / "failure.json", {**value, "status": failure})

    def over(self):
        return self.elapsed() > self.cfg["max_seconds"] or self.peak > self.cfg["max_process_tree_bytes"]

    def watchdog(self):
        while not self.done.wait(1):
            self.peak = max(self.peak, self.process.memory_info().rss)
            if self.over():
                self.save("RESOURCE_LIMIT")
                os._exit(70)

    def guard(self):
        self.peak = max(self.peak, self.process.memory_info().rss)
        if self.over():
            self.save("RESOURCE_LIMIT")
            raise RuntimeError("resource limit")

    def close(self):
        self.done.set()
        if self.thread.is_alive():
            self.thread.join()
        self.save()


class Run:
    def __init__(self):
        self.identity = check_review()
        self.cfg = base.read_json(FILES["config"])
        self.oldcfg = base.read_json(base.FILES["config"])
        c = self.cfg
        base.require(c["ns"] == self.oldcfg["ns"] == [0, 5, 10, 30]
            and c["policies"] == self.oldcfg["policies"] and c["top_n"] == 2
            and (c["bootstrap_repeats"], c["bootstrap_seed"], c["family_size"], c["alpha"]) == (5000, 20260907, 9, .05)
            and c["direction_threshold"] == 0 and c["practical_adoption_margin"] is None
            and c["stop_if_any_supply_shortage"], "conditional analysis changed")
        self.root = ROOT / c["output_root"]
        base.require(self.root.resolve() == (ROOT / "outputs/recommendation-evidence/rec-ev-032/conditional-ranking").resolve(),
                     "unexpected output directory")
        self.paths, self.seals = {}, {}
        self.budget = None
        self.started = False

    def guard(self):
        base.require(check_review() == self.identity, "execution fingerprint changed")
        if self.budget:
            self.budget.guard()

    def sources(self):
        self.guard()
        for name, spec in self.cfg["inputs"].items():
            p = ROOT / spec["path"]
            base.require(p.resolve().is_relative_to(ROOT.resolve()), "source outside research repo")
            base.require(base.pin(p) == {"bytes": spec["bytes"], "sha256": spec["sha256"]}, "input drift: " + name)
            self.paths[name] = p
        self.seals = {s: base.read_json(self.paths[s + "_seal"]) for s in ("prepare", "train", "score", "evaluate")}
        for s in self.seals.values():
            base.require(s["status"] == "COMPLETE" and s["fingerprint"] == base.fingerprint(), "original fingerprint drift")
        base.require(self.seals["train"]["prepare_seal"] == base.pin(self.paths["prepare_seal"])
            and self.seals["score"]["prepare_seal"] == base.pin(self.paths["prepare_seal"])
            and self.seals["score"]["train_seal"] == base.pin(self.paths["train_seal"])
            and self.seals["evaluate"]["score_seal"] == base.pin(self.paths["score_seal"]), "original dependency drift")
        mapping = [("prepared", "prepare", "prepared.npz"), ("profiles", "prepare", "profiles.parquet"),
            ("membership", "prepare", "evaluation-membership.parquet"), ("factors", "train", "factors.npz"),
            ("original_rankings", "score", "rankings.npz"), ("original_diagnostics", "score", "score-diagnostics.parquet"),
            ("original_metrics", "evaluate", "metrics.json"), ("original_user_metrics", "evaluate", "user-metrics.parquet")]
        for name, phase, filename in mapping:
            base.require(base.pin(self.paths[name]) == self.seals[phase]["outputs"][filename], "original output drift")
        base.require(self.cfg["inputs"]["labels"] == self.seals["evaluate"]["label_source"] == self.oldcfg["inputs"]["labels"]
            and self.cfg["inputs"]["structured"] == self.seals["prepare"]["sources"]["structured"]
            == self.oldcfg["inputs"]["structured"], "provenance drift")

    def prepare(self):
        with np.load(self.paths["prepared"], allow_pickle=False) as z:
            self.ids, self.bayes, self.prior, train_ids = z["item_ids"], z["bayes"], z["prior"], z["training_user_ids"]
        base.require(len(self.ids) == self.cfg["expected_items"] and np.all(np.diff(self.ids) > 0)
            and self.bayes.shape == self.ids.shape and np.isfinite(self.bayes).all()
            and self.prior.shape == (10,) and np.isfinite(self.prior).all(), "prepared data invalid")
        self.profiles = pd.read_parquet(self.paths["profiles"],
            columns=["user_key", "profile_movie_ids", "profile_rating_indices"])
        self.keys = self.profiles.user_key.to_numpy(dtype=str)
        with np.load(self.paths["original_rankings"], allow_pickle=False) as z:
            self.old_top2 = z["movie_ids"]
            base.require(np.array_equal(self.keys, z["user_keys"]) and z["ns"].tolist() == self.cfg["ns"], "original cohort/order")
        base.require(len(self.keys) == self.cfg["expected_users"] and len(set(self.keys)) == len(self.keys)
            and self.keys.tolist() == sorted(self.keys), "cohort changed")
        base.require(len(train_ids) == self.oldcfg["expected_train_users"] and len(set(train_ids)) == len(train_ids)
            and all(base.calibration_user(int(uid)) for uid in train_ids)
            and not set(map(base.user_key, map(int, train_ids))).intersection(self.keys), "training/evaluation roles")
        self.members = pd.read_parquet(self.paths["membership"], columns=["outer", "user_key", "target_movie_ids"])
        base.require(len(self.members) == self.cfg["expected_membership_rows"]
            and set(self.members.user_key) == set(self.keys) and set(self.members.outer) <= set(base.OUTERS)
            and not self.members.duplicated(["outer", "user_key"]).any(), "membership changed")
        self.expected, unions = {}, {key: set() for key in self.keys}
        for row in self.members.itertuples(index=False):
            values = list(map(int, row.target_movie_ids))
            base.require(len(values) == len(set(values)), "duplicate target in outer")
            self.expected[(row.outer, row.user_key)] = set(values)
            unions[row.user_key].update(values)
        self.targets = [np.asarray(sorted(unions[key]), dtype=np.int64) for key in self.keys]
        base.require(sum(map(len, self.targets)) == self.cfg["expected_targets"], "target union changed")
        catalogue = set(self.ids)
        for row, target in zip(self.profiles.itertuples(index=False), self.targets, strict=True):
            movies, indices = list(map(int, row.profile_movie_ids)), np.asarray(row.profile_rating_indices)
            base.require(len(movies) == len(set(movies)) == len(indices) == 30 and set(movies) <= catalogue
                and np.all((indices >= 0) & (indices <= 9) & (indices == indices.astype(np.int64)))
                and set(target) <= catalogue and len(target) >= 2
                and not set(movies).intersection(target), "E/O/catalogue/grid failure")
        self.offsets = np.concatenate(([0], np.cumsum(list(map(len, self.targets))))).astype(np.int64)
        self.target_ids = np.concatenate(self.targets)
        self.guard()
        pd.DataFrame({"user_key": self.keys, "movie_ids": self.targets}).to_parquet(self.root / "targets.parquet", index=False)
        base.write_json(self.root / "request-seal.json", {"status": "SEALED_BEFORE_SCORING", "fingerprint": self.identity,
            "source_membership": base.pin(self.paths["membership"]), "users": len(self.keys),
            "unique_targets": len(self.target_ids), "min_targets": min(map(len, self.targets)),
            "max_targets": max(map(len, self.targets)), "profile_overlap": 0, "label_payload_opened": False,
            "outputs": {"targets.parquet": base.pin(self.root / "targets.parquet")}})

    def score(self):
        x = sparse.load_npz(self.paths["structured"]).astype(np.float64).tocsr()
        base.require(list(x.shape) == self.seals["prepare"]["content_shape"] and np.isfinite(x.data).all(), "feature invalid")
        with np.load(self.paths["factors"], allow_pickle=False) as z:
            fids, fy = z["item_ids"], z["factors"]
        base.require(np.all(np.diff(fids) > 0) and fy.shape == (len(fids), self.oldcfg["als"]["rank"])
                     and np.isfinite(fy).all(), "factors invalid")
        match = np.searchsorted(fids, self.ids)
        has = (match < len(fids)) & (fids[np.minimum(match, len(fids) - 1)] == self.ids)
        y = np.zeros((len(self.ids), self.oldcfg["als"]["rank"]), dtype=np.float64)
        y[has] = fy[match[has]]
        ns = self.cfg["ns"]
        self.ranked = np.full((len(self.keys), 4, 2, 2), -1, dtype=np.int64)
        actual_top2 = np.full_like(self.ranked, -1)
        counts = np.zeros((len(self.keys), 4, 2), dtype=np.int64)
        global_ranks = np.zeros((len(self.target_ids), 4, 2), dtype=np.int64)
        global_scores = np.zeros((len(self.target_ids), 4, 2), dtype=np.float64)
        digests = [hashlib.sha256() for _ in range(8)]
        order_digests = [hashlib.sha256() for _ in range(8)]
        diagnostics = []
        old_diag = pd.read_parquet(self.paths["original_diagnostics"]).set_index(["user_key", "n"])
        for u, row in enumerate(self.profiles.itertuples(index=False)):
            all_movies = np.asarray(row.profile_movie_ids, dtype=np.int64)
            all_indices = np.asarray(row.profile_rating_indices, dtype=np.int64)
            target = self.targets[u]
            positions = np.searchsorted(self.ids, target)
            section = slice(self.offsets[u], self.offsets[u + 1])
            for k, n in enumerate(ns):
                orders, scores, diag = score_one(self.ids, self.bayes, self.prior, x, y, has,
                    all_movies, all_indices, n, self.oldcfg["als"]["reg"])
                for field, value in diag.items():
                    base.require(old_diag.loc[(row.user_key, n), field] == value, "original diagnostic mismatch: " + field)
                for p in range(2):
                    digests[2 * k + p].update(scores[p].astype("<f8").tobytes())
                    # Length-prefixed full position order for each sorted user.
                    order_digests[2 * k + p].update(np.array([len(orders[p])], dtype="<i8").tobytes())
                    order_digests[2 * k + p].update(orders[p].astype("<i8").tobytes())
                    actual_top2[u, k, p] = self.ids[orders[p][:2]]
                    selected, ranks = projected_order(orders[p], positions, len(self.ids))
                    counts[u, k, p] = len(selected)
                    self.ranked[u, k, p, :len(selected)] = target[selected]
                    global_ranks[section, k, p] = ranks
                    global_scores[section, k, p] = scores[p][positions]
                    diagnostics.append({"user_key": row.user_key, "n": n, "policy": self.cfg["policies"][p],
                        "targets": len(target), "supported_targets": int((ranks > 0).sum()),
                        "supplied": len(selected), "global_order_size": len(orders[p])})
            if (u + 1) % 100 == 0:
                self.guard()
                print({"phase": "CONDITIONAL_SCORE", "users": u + 1, "seconds": round(self.budget.elapsed(), 2)}, flush=True)
        actual_digests = [h.hexdigest() for h in digests]
        verify_original_scores(actual_digests, self.seals["score"]["full_score_sha256"], actual_top2, self.old_top2)
        base.require(np.array_equal(self.ranked[:, 0, 0], self.ranked[:, 0, 1]), "n0 projection differs")
        self.guard()
        pd.DataFrame(diagnostics).to_parquet(self.root / "supply.parquet", index=False)
        base.write_json(self.root / "supply-summary.json", {"users": len(self.keys), "cells": int(counts.size),
            "shortage_cells": int((counts < 2).sum()), "original_full_scores_match": True, "original_top2_match": True})
        check_supply(counts)
        np.savez_compressed(self.root / "rankings.npz", user_keys=self.keys, ns=np.asarray(ns), movie_ids=self.ranked)
        np.savez_compressed(self.root / "target-order.npz", user_keys=self.keys, offsets=self.offsets,
            movie_ids=self.target_ids, global_ranks=global_ranks, global_scores=global_scores)
        self.guard()
        names = SCORE_OUTPUTS
        base.write_json(self.root / "score-seal.json", {"status": "COMPLETE", "fingerprint": self.identity,
            "request_seal": base.pin(self.root / "request-seal.json"),
            "original_score_seal": base.pin(self.paths["score_seal"]),
            "full_score_sha256": actual_digests, "full_order_sha256": [h.hexdigest() for h in order_digests],
            "original_top2_matches": True, "original_diagnostics_match": True, "label_payload_opened": False,
            "outputs": {name: base.pin(self.root / name) for name in names}})

    def validate_score(self):
        self.guard()
        seal = base.read_json(self.root / "score-seal.json")
        base.require(seal["status"] == "COMPLETE" and seal["fingerprint"] == self.identity
            and seal["request_seal"] == base.pin(self.root / "request-seal.json")
            and seal["original_score_seal"] == base.pin(self.paths["score_seal"])
            and seal["full_score_sha256"] == self.seals["score"]["full_score_sha256"]
            and set(seal["outputs"]) == set(SCORE_OUTPUTS), "conditional score not sealed")
        request = base.read_json(self.root / "request-seal.json")
        base.require(request["status"] == "SEALED_BEFORE_SCORING" and request["fingerprint"] == self.identity
            and request["source_membership"] == base.pin(self.paths["membership"])
            and set(request["outputs"]) == {"targets.parquet"}
            and request["outputs"]["targets.parquet"] == base.pin(self.root / "targets.parquet"), "request seal drift")
        for name, expected in seal["outputs"].items():
            base.require(base.pin(self.root / name) == expected, "modified scored output")

    def load_labels(self):
        self.validate_score()
        self.sources()
        labels = pd.read_parquet(self.paths["labels"],
            columns=["outer", "user_key", "target_movie_ids", "target_rating_indices", "target_q",
                     "full_history_count", "full_history_histogram"],
            filters=[("user_key", "in", self.keys.tolist()), ("outer", "in", list(base.OUTERS))])
        base.require(len(labels) == self.cfg["expected_membership_rows"]
            and not labels.duplicated(["outer", "user_key"]).any()
            and set(zip(labels.outer, labels.user_key)) == set(self.expected), "label membership mismatch")
        maps, hists, slots = {key: {} for key in self.keys}, {}, 0
        for row in labels.itertuples(index=False):
            movies = list(map(int, row.target_movie_ids))
            indices = np.asarray(row.target_rating_indices)
            qs = np.asarray(row.target_q, dtype=np.float64)
            hist = tuple(map(int, row.full_history_histogram))
            base.require(len(movies) == len(set(movies)) == len(indices) == len(qs)
                and set(movies) == self.expected[(row.outer, row.user_key)]
                and sum(hist) == row.full_history_count
                and (row.user_key not in hists or hists[row.user_key] == hist), "label/H mismatch")
            hists[row.user_key] = hist
            for movie, index, q in zip(movies, indices, qs, strict=True):
                base.require(np.isfinite(index) and index == int(index) and 0 <= index <= 9, "label grid")
                raw, fixed = (int(index) + 1) / 2, base.q_from_hist(int(index), hist)
                base.require(np.isfinite(q) and abs(q - fixed) < 1e-12, "fixed Q mismatch")
                value = (raw, float(q))
                previous = maps[row.user_key].get(movie)
                base.require(previous is None or previous == value, "duplicate label disagreement")
                maps[row.user_key][movie] = value
            slots += len(movies)
        base.require(slots == self.cfg["expected_label_slots"]
            and all(set(maps[key]) == set(target) for key, target in zip(self.keys, self.targets, strict=True)), "E label coverage")
        old = pd.read_parquet(self.paths["original_user_metrics"],
            columns=["user_key", "movie_ids", "q", "raw_ratings"])
        checked = set()
        for row in old.itertuples(index=False):
            for movie, q, raw in zip(row.movie_ids, row.q, row.raw_ratings, strict=True):
                if not pd.isna(q):
                    base.require(maps[row.user_key][int(movie)] == (float(raw), float(q)), "old observed label changed")
                    checked.add((row.user_key, int(movie)))
        label_rows = [{"user_key": key, "movie_id": movie, "rating_raw": value[0], "q": value[1]}
                      for key in self.keys for movie, value in sorted(maps[key].items())]
        self.guard()
        pd.DataFrame(label_rows).to_parquet(self.root / "evaluation-labels.parquet", index=False)
        np.savez_compressed(self.root / "histograms.npz", user_keys=self.keys,
                            histograms=np.array([hists[key] for key in self.keys]))
        return maps, len(checked), hists

    def evaluate(self):
        maps, checked, hists = self.load_labels()
        rows, differences = [], np.zeros((len(self.keys), 3, 3, 2))
        for u, key in enumerate(self.keys):
            for k, n in enumerate(self.cfg["ns"]):
                for p, policy in enumerate(self.cfg["policies"]):
                    movies = self.ranked[u, k, p].tolist()
                    base.require(len(set(movies)) == 2 and all(m in maps[key] for m in movies), "unscorable conditional top2")
                    values = [maps[key][m] for m in movies]
                    raw, qs = [v[0] for v in values], [v[1] for v in values]
                    bounds = base.metric_bounds(qs)
                    base.require(np.array_equal(bounds[:, 0], bounds[:, 1]), "conditional bounds not points")
                    numerator, denominator = metric_parts(raw, hists[key])
                    points = numerator / denominator
                    base.require(np.allclose(points, bounds[:, 0], atol=1e-12, rtol=0), "metric/H crosscheck failed")
                    rows.append({"user_key": key, "n": n, "policy": policy, "movie_ids": movies,
                                 "raw_ratings": raw, "q": qs, **dict(zip(METRICS, points.tolist()))})
                if k:
                    differences[u, k - 1] = observed_difference(
                        [maps[key][int(m)][0] for m in self.ranked[u, k, 1]],
                        [maps[key][int(m)][0] for m in self.ranked[u, k, 0]], hists[key])
        frame = pd.DataFrame(rows)
        aggregate = [{"n": int(n), "policy": policy, "users": len(group), "slots": 2 * len(group),
                      "label_coverage": 1., "metrics": {m: float(group[m].mean()) for m in METRICS}}
                     for (n, policy), group in frame.groupby(["n", "policy"], sort=True)]
        self.guard()
        intervals = base.ci_bounds(differences, 5000, 20260907, .05, 9)
        comparisons = []
        for k, n in enumerate(self.cfg["ns"][1:]):
            for j, metric in enumerate(METRICS):
                lo, hi = intervals[k, j].tolist()
                comparisons.append({"n": n, "metric": metric, "difference_M0_minus_P0": float(differences[:, k, j, 0].mean()),
                    "simultaneous_exploratory_ci": [lo, hi], "ci_width": hi - lo,
                    "status": direction(metric, lo, hi)})
        self.guard()
        frame.to_parquet(self.root / "user-metrics.parquet", index=False)
        base.write_json(self.root / "metrics.json", {"amendment": self.cfg["amendment"],
            "claim_scope": "EXPLORATORY_ORIGINAL_ORDER_WITHIN_OBSERVED_E_NOT_FULL_CATALOG_TOP2",
            "users": len(self.keys), "unique_targets": len(self.target_ids), "metrics": aggregate,
            "paired_differences": comparisons, "old_observed_pairs_crosschecked": checked,
            "label_q_verified_from_fixed_H": True, "original_full_scores_match": True,
            "original_full_catalog_top2_match": True, "original_diagnostics_match": True,
            "training_runs": 0, "fixed_score_reproduction_runs": 1, "new_model_variants": 0,
            "extension_labels_used": False, "new_confirmation": False,
            "product_adopted": False, "final_K_selected": False})

    def run(self):
        self.sources()
        complete = self.root / "completion-seal.json"
        if complete.exists():
            seal = base.read_json(complete)
            base.require(seal["status"] == "COMPLETE" and seal["fingerprint"] == self.identity
                and set(seal["outputs"]) == set(OUTPUTS)
                and seal["original_score_seal"] == base.pin(self.paths["score_seal"])
                and seal["original_evaluate_seal"] == base.pin(self.paths["evaluate_seal"]), "completion drift")
            for name, expected in seal["outputs"].items():
                base.require(base.pin(self.root / name) == expected, "completion artifact drift")
            self.validate_score()
            print("VERIFIED_EXISTING_COMPLETION_NO_RESCORING")
            return
        base.require(not self.root.exists() or not any(self.root.iterdir()), "partial run exists; preserve without retry")
        self.root.mkdir(parents=True, exist_ok=True)
        self.started = True
        self.budget = Budget(self.root, self.identity, self.cfg)
        self.budget.save()
        self.budget.thread.start()
        try:
            self.prepare()
            self.score()
            self.evaluate()
            self.sources()
            self.guard()
            self.budget.close()
            names = OUTPUTS
            self.guard()
            base.write_json(complete, {"status": "COMPLETE", "fingerprint": self.identity,
                "original_score_seal": base.pin(self.paths["score_seal"]),
                "original_evaluate_seal": base.pin(self.paths["evaluate_seal"]),
                "outputs": {name: base.pin(self.root / name) for name in names},
                "seconds": self.budget.elapsed(), "peak_rss_bytes": self.budget.peak,
                "original_files_preserved": True, "new_raw_or_protected_labels_opened": False})
            print({"phase": "CONDITIONAL_COMPLETE", "seconds": round(self.budget.elapsed(), 3)}, flush=True)
        except Exception as exc:
            self.budget.close()
            failure = self.root / "failure.json"
            if not failure.exists():
                base.write_json(failure, {"status": "FAILED", "fingerprint": self.identity,
                    "error_type": type(exc).__name__, "error": str(exc)})
            raise


if __name__ == "__main__":
    Run().run()
