"""One fixed ALS checkpoint, projected onto the unchanged observed evaluation E."""
from __future__ import annotations
import hashlib
from pathlib import Path
import rec_ev_032_basic as base  # Set BLAS limits before numpy is imported.
import rec_ev_032_conditional as prior
import numpy as np
import pandas as pd

ROOT = base.ROOT
PLAN = ROOT / "docs/recommendation/experiments/rec-ev-032/als-only"
FILES = {"design": PLAN / "README.md", "config": PLAN / "config.json", "runner": Path(__file__).resolve(),
         "tests": ROOT / "scripts/tests/test_rec_ev_032_als_only.py",
         "base_runner": base.FILES["runner"], "conditional_runner": prior.FILES["runner"]}
SCORE_OUTPUTS = ("rankings.npz", "target-order.npz", "supply.parquet", "supply-summary.json")
OUTPUTS = ("request-seal.json", *SCORE_OUTPUTS, "score-seal.json", "user-metrics.parquet", "metrics.json", "budget.json")


def fingerprint():
    return {key: base.sha(path) for key, path in FILES.items()}


def check_review():
    prior.check_review()
    identity = fingerprint()
    review = base.read_json(PLAN / "review.json")
    base.require(review["status"] == "PASS" and review["fingerprint"] == identity, "ALS review absent or stale")
    return identity


def als_order(ids, bayes, factors, has, movies, indices, n, reg):
    # No E, histogram, evaluation labels, or content features enter scoring.
    positions = np.searchsorted(ids, movies[:n])
    available = np.ones(len(ids), dtype=bool)
    available[positions] = False
    use = has[positions]
    count = int(use.sum())
    p = base.fold_in(factors[positions[use]], (indices[:n][use] + 1) / 2, reg) if n else None
    active = p is not None and np.linalg.norm(p) > 1e-12
    if active:
        values = factors @ p
        base.require(np.isfinite(values).all(), "nonfinite ALS score")
        mask = available & has
        fallback = "NONE"
    else:
        values, mask = bayes, available
        fallback = "P0_NO_INPUT" if n == 0 else ("P0_NO_FACTOR" if count == 0 else "P0_ZERO_PROFILE")
    values = np.where(mask, values, -np.inf)
    ordered = base.order(values, ids)
    base.require(len(ordered) >= 2, "global ALS supply failure")
    return ordered, values, {"n_factor": count, "als_active": bool(active), "fallback": fallback}


def validate_completion(root, identity, source_completion):
    seal = base.read_json(root / "completion-seal.json")
    base.require(seal["status"] == "COMPLETE" and seal["fingerprint"] == identity
                 and seal["source_completion"] == source_completion and set(seal["outputs"]) == set(OUTPUTS),
                 "completion drift")
    for name, expected in seal["outputs"].items():
        base.require(base.pin(root / name) == expected, "completion output drift")


class Run:
    def __init__(self):
        self.identity = check_review()
        self.cfg = base.read_json(FILES["config"])
        self.oldcfg = base.read_json(base.FILES["config"])
        c = self.cfg
        base.require(c["ns"] == self.oldcfg["ns"] == [0, 5, 10, 30]
            and c["policies"] == self.oldcfg["policies"] + ["A0_RAW_ALS_ONLY"] and c["top_n"] == 2
            and (c["bootstrap_repeats"], c["bootstrap_seed"], c["family_size"], c["alpha"]) == (5000, 20260907, 18, .05)
            and c["direction_threshold"] == 0 and c["practical_adoption_margin"] is None
            and c["stop_if_any_supply_shortage"], "fixed analysis changed")
        self.root = ROOT / c["output_root"]
        base.require(self.root.resolve() == (ROOT / "outputs/recommendation-evidence/rec-ev-032/als-only").resolve(),
                     "unexpected output directory")
        self.paths, self.budget = {}, None

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
        seals = {key: base.read_json(self.paths[key + "_seal"]) for key in ("prepare", "train", "score", "evaluate")}
        for seal in seals.values():
            base.require(seal["status"] == "COMPLETE" and seal["fingerprint"] == base.fingerprint(), "base fingerprint drift")
        for name, phase, output in (("prepared", "prepare", "prepared.npz"), ("profiles", "prepare", "profiles.parquet"),
            ("membership", "prepare", "evaluation-membership.parquet"), ("factors", "train", "factors.npz"),
            ("original_diagnostics", "score", "score-diagnostics.parquet")):
            base.require(seals[phase]["outputs"][output] == base.pin(self.paths[name]), "base source chain drift")
        base.require(seals["train"]["prepare_seal"] == base.pin(self.paths["prepare_seal"])
            and seals["score"]["prepare_seal"] == base.pin(self.paths["prepare_seal"])
            and seals["score"]["train_seal"] == base.pin(self.paths["train_seal"])
            and seals["evaluate"]["score_seal"] == base.pin(self.paths["score_seal"]), "base dependency drift")
        complete = base.read_json(self.paths["conditional_completion-seal.json"])
        base.require(complete["status"] == "COMPLETE" and complete["fingerprint"] == prior.fingerprint()
            and set(complete["outputs"]) == set(prior.OUTPUTS)
            and complete["original_score_seal"] == base.pin(self.paths["score_seal"])
            and complete["original_evaluate_seal"] == base.pin(self.paths["evaluate_seal"]), "conditional completion drift")
        for name, expected in complete["outputs"].items():
            base.require(base.pin(self.paths["conditional_" + name]) == expected, "conditional source drift")
        request = base.read_json(self.paths["conditional_request-seal.json"])
        score = base.read_json(self.paths["conditional_score-seal.json"])
        base.require(request["status"] == "SEALED_BEFORE_SCORING" and request["fingerprint"] == prior.fingerprint()
            and request["source_membership"] == base.pin(self.paths["membership"])
            and request["outputs"] == {"targets.parquet": base.pin(self.paths["conditional_targets.parquet"])}
            and score["status"] == "COMPLETE" and score["fingerprint"] == prior.fingerprint()
            and score["request_seal"] == base.pin(self.paths["conditional_request-seal.json"])
            and score["original_score_seal"] == base.pin(self.paths["score_seal"])
            and score["full_score_sha256"] == seals["score"]["full_score_sha256"]
            and set(score["outputs"]) == set(prior.SCORE_OUTPUTS), "conditional dependency drift")
        for name, expected in score["outputs"].items():
            base.require(base.pin(self.paths["conditional_" + name]) == expected, "conditional score output drift")

    def prepare(self):
        with np.load(self.paths["prepared"], allow_pickle=False) as z:
            self.ids, self.bayes, train_ids = z["item_ids"], z["bayes"], z["training_user_ids"]
        base.require(len(self.ids) == self.cfg["expected_items"] and np.all(np.diff(self.ids) > 0)
            and self.bayes.shape == self.ids.shape and np.isfinite(self.bayes).all(), "prepared data invalid")
        self.profiles = pd.read_parquet(self.paths["profiles"], columns=["user_key", "profile_movie_ids", "profile_rating_indices"])
        self.keys = self.profiles.user_key.to_numpy(dtype=str)
        base.require(len(self.keys) == self.cfg["expected_users"] and len(set(self.keys)) == len(self.keys)
            and self.keys.tolist() == sorted(self.keys), "cohort changed")
        base.require(len(train_ids) == self.oldcfg["expected_train_users"] and len(set(train_ids)) == len(train_ids)
            and all(base.calibration_user(int(uid)) for uid in train_ids)
            and not set(map(base.user_key, map(int, train_ids))).intersection(self.keys), "training/evaluation roles")
        members = pd.read_parquet(self.paths["membership"], columns=["outer", "user_key", "target_movie_ids"])
        base.require(len(members) == self.cfg["expected_membership_rows"] and set(members.user_key) == set(self.keys)
            and set(members.outer) <= set(base.OUTERS) and not members.duplicated(["outer", "user_key"]).any(), "membership changed")
        unions = {key: set() for key in self.keys}
        slots = 0
        for row in members.itertuples(index=False):
            movies = list(map(int, row.target_movie_ids))
            base.require(len(movies) == len(set(movies)), "duplicate target")
            unions[row.user_key].update(movies)
            slots += len(movies)
        self.targets = [np.array(sorted(unions[key]), dtype=np.int64) for key in self.keys]
        base.require(slots == self.cfg["expected_label_slots"] and sum(map(len, self.targets)) == self.cfg["expected_targets"], "E changed")
        targets = pd.read_parquet(self.paths["conditional_targets.parquet"])
        base.require(np.array_equal(targets.user_key.to_numpy(dtype=str), self.keys)
            and all(np.array_equal(a, b) for a, b in zip(targets.movie_ids, self.targets, strict=True)), "conditional E changed")
        catalogue = set(self.ids)
        for row, target in zip(self.profiles.itertuples(index=False), self.targets, strict=True):
            movies, indices = list(map(int, row.profile_movie_ids)), np.asarray(row.profile_rating_indices)
            base.require(len(movies) == len(set(movies)) == len(indices) == 30 and set(movies) <= catalogue
                and np.all((indices >= 0) & (indices <= 9) & (indices == indices.astype(np.int64)))
                and set(target) <= catalogue and len(target) >= 2 and not set(movies).intersection(target), "E/O/catalogue/grid failure")
        self.offsets = np.concatenate(([0], np.cumsum(list(map(len, self.targets))))).astype(np.int64)
        self.target_ids = np.concatenate(self.targets)
        with np.load(self.paths["conditional_rankings.npz"], allow_pickle=False) as z:
            self.reference = z["movie_ids"]
            base.require(np.array_equal(self.keys, z["user_keys"]) and z["ns"].tolist() == self.cfg["ns"]
                and self.reference.shape == (len(self.keys), 4, 2, 2), "reference rankings changed")
        self.guard()
        base.write_json(self.root / "request-seal.json", {"status": "SEALED_BEFORE_SCORING", "fingerprint": self.identity,
            "source_completion": base.pin(self.paths["conditional_completion-seal.json"]),
            "source_targets": base.pin(self.paths["conditional_targets.parquet"]),
            "source_membership": base.pin(self.paths["membership"]), "users": len(self.keys),
            "unique_targets": len(self.target_ids), "label_payload_opened": False})

    def score(self):
        with np.load(self.paths["factors"], allow_pickle=False) as z:
            fids, fy = z["item_ids"], z["factors"]
        base.require(np.all(np.diff(fids) > 0) and fy.shape == (len(fids), self.oldcfg["als"]["rank"])
            and np.isfinite(fy).all(), "invalid factors")
        match = np.searchsorted(fids, self.ids)
        has = (match < len(fids)) & (fids[np.minimum(match, len(fids) - 1)] == self.ids)
        y = np.zeros((len(self.ids), self.oldcfg["als"]["rank"]), dtype=np.float64)
        y[has] = fy[match[has]]
        self.ranked = np.full((len(self.keys), 4, 3, 2), -1, dtype=np.int64)
        self.ranked[:, :, :2] = self.reference
        ranks = np.zeros((len(self.target_ids), 4), dtype=np.int64)
        scores = np.full((len(self.target_ids), 4), -np.inf, dtype=np.float64)
        counts = np.zeros((len(self.keys), 4), dtype=np.int64)
        score_hashes, order_hashes = [hashlib.sha256() for _ in range(4)], [hashlib.sha256() for _ in range(4)]
        old_diag = pd.read_parquet(self.paths["original_diagnostics"]).set_index(["user_key", "n"])
        rows = []
        for u, row in enumerate(self.profiles.itertuples(index=False)):
            movies, indices = np.asarray(row.profile_movie_ids, dtype=np.int64), np.asarray(row.profile_rating_indices, dtype=np.int64)
            target = self.targets[u]
            positions = np.searchsorted(self.ids, target)
            section = slice(self.offsets[u], self.offsets[u + 1])
            for k, n in enumerate(self.cfg["ns"]):
                ordered, values, diag = als_order(self.ids, self.bayes, y, has, movies, indices, n, self.oldcfg["als"]["reg"])
                base.require(all(old_diag.loc[(row.user_key, n), field] == diag[field] for field in ("n_factor", "als_active")),
                             "original ALS diagnostics mismatch")
                score_hashes[k].update(values.astype("<f8").tobytes())
                order_hashes[k].update(np.array([len(ordered)], dtype="<i8").tobytes())
                order_hashes[k].update(ordered.astype("<i8").tobytes())
                selected, global_ranks = prior.projected_order(ordered, positions, len(self.ids))
                counts[u, k] = len(selected)
                self.ranked[u, k, 2, :len(selected)] = target[selected]
                ranks[section, k], scores[section, k] = global_ranks, values[positions]
                rows.append({"user_key": row.user_key, "n": n, **diag, "targets": len(target),
                    "factor_targets": int(has[positions].sum()), "supported_targets": int((global_ranks > 0).sum()),
                    "supplied": len(selected), "global_order_size": len(ordered),
                    "p0_factorless_slots": int((~has[np.searchsorted(self.ids, self.reference[u, k, 0])]).sum()),
                    "m0_factorless_slots": int((~has[np.searchsorted(self.ids, self.reference[u, k, 1])]).sum())})
            if (u + 1) % 100 == 0:
                self.guard()
                print({"phase": "ALS_ONLY_SCORE", "users": u + 1, "seconds": round(self.budget.elapsed(), 2)}, flush=True)
        supply = pd.DataFrame(rows)
        supply.to_parquet(self.root / "supply.parquet", index=False)
        summary = [{"n": int(n), "users": len(group), "targets": int(group.targets.sum()),
            "factor_targets": int(group.factor_targets.sum()), "supported_targets": int(group.supported_targets.sum()),
            "supplied_slots": int(group.supplied.sum()), "p0_factorless_slots": int(group.p0_factorless_slots.sum()),
            "m0_factorless_slots": int(group.m0_factorless_slots.sum()),
            "fallback_counts": {str(key): int(value) for key, value in group.fallback.value_counts().items()}}
            for n, group in supply.groupby("n", sort=True)]
        base.write_json(self.root / "supply-summary.json", {"cells": int(counts.size), "shortage_cells": int((counts < 2).sum()),
            "catalogue_items": len(self.ids), "catalogue_factor_items": int(has.sum()), "by_n": summary})
        prior.check_supply(counts)
        base.require(np.array_equal(self.ranked[:, 0, 2], self.reference[:, 0, 0]), "n0 differs from P0")
        np.savez_compressed(self.root / "rankings.npz", user_keys=self.keys, ns=np.asarray(self.cfg["ns"]),
            policies=np.asarray(self.cfg["policies"]), movie_ids=self.ranked)
        np.savez_compressed(self.root / "target-order.npz", user_keys=self.keys, offsets=self.offsets,
            movie_ids=self.target_ids, global_ranks=ranks, global_scores=scores, factor_supported=has[np.searchsorted(self.ids, self.target_ids)])
        self.guard()
        base.write_json(self.root / "score-seal.json", {"status": "COMPLETE", "fingerprint": self.identity,
            "source_completion": base.pin(self.paths["conditional_completion-seal.json"]),
            "request_seal": base.pin(self.root / "request-seal.json"), "source_factors": base.pin(self.paths["factors"]),
            "full_score_sha256": [v.hexdigest() for v in score_hashes], "full_order_sha256": [v.hexdigest() for v in order_hashes],
            "original_als_diagnostics_match": True, "label_payload_opened": False,
            "outputs": {name: base.pin(self.root / name) for name in SCORE_OUTPUTS}})

    def validate_score(self):
        self.guard()
        request = base.read_json(self.root / "request-seal.json")
        score = base.read_json(self.root / "score-seal.json")
        source = base.pin(self.paths["conditional_completion-seal.json"])
        base.require(request["status"] == "SEALED_BEFORE_SCORING" and request["fingerprint"] == self.identity
            and request["source_completion"] == source and request["source_targets"] == base.pin(self.paths["conditional_targets.parquet"])
            and request["source_membership"] == base.pin(self.paths["membership"])
            and score["status"] == "COMPLETE" and score["fingerprint"] == self.identity
            and score["request_seal"] == base.pin(self.root / "request-seal.json") and score["source_completion"] == source
            and score["source_factors"] == base.pin(self.paths["factors"])
            and set(score["outputs"]) == set(SCORE_OUTPUTS), "unsealed ALS scores")
        for name, expected in score["outputs"].items():
            base.require(base.pin(self.root / name) == expected, "score output drift")

    def evaluate(self):
        self.validate_score()
        self.sources()
        labels = pd.read_parquet(self.paths["conditional_evaluation-labels.parquet"])
        with np.load(self.paths["conditional_histograms.npz"], allow_pickle=False) as z:
            hists = z["histograms"]
            base.require(np.array_equal(z["user_keys"], self.keys) and hists.shape == (len(self.keys), 10)
                and np.all(hists >= 0) and np.all(hists == hists.astype(np.int64)) and np.all(hists.sum(axis=1) > 0), "H invalid")
        base.require(len(labels) == len(self.target_ids) and not labels.duplicated(["user_key", "movie_id"]).any()
            and set(labels.user_key) == set(self.keys), "evaluation labels changed")
        maps = {}
        key_pos = {key: u for u, key in enumerate(self.keys)}
        for key, group in labels.groupby("user_key", sort=False):
            u = key_pos[key]
            base.require(set(group.movie_id) == set(self.targets[u]), "E label mismatch")
            entries = {}
            for row in group.itertuples(index=False):
                index = base.rating_index(row.rating_raw)
                q = base.q_from_hist(index, hists[u])
                base.require(np.isfinite(row.q) and abs(q - row.q) < 1e-12, "Q mismatch")
                entries[int(row.movie_id)] = (float(row.rating_raw), float(q))
            maps[key] = entries
        old = pd.read_parquet(self.paths["conditional_user-metrics.parquet"]).set_index(["user_key", "n", "policy"])
        base.require(len(old) == len(self.keys) * 8 and old.index.is_unique, "reference metric cohort changed")
        differences = np.zeros((len(self.keys), 3, 2, 3, 2), dtype=np.float64)
        rows = []
        for u, key in enumerate(self.keys):
            for k, n in enumerate(self.cfg["ns"]):
                raw_by_policy = []
                for p, policy in enumerate(self.cfg["policies"]):
                    movies = self.ranked[u, k, p].tolist()
                    base.require(len(set(movies)) == 2 and all(m in maps[key] for m in movies), "unscorable top2")
                    raw, qs = zip(*(maps[key][m] for m in movies), strict=True)
                    numerator, denominator = prior.metric_parts(raw, hists[u])
                    values = numerator / denominator
                    raw_by_policy.append(raw)
                    if p < 2:
                        ref = old.loc[(key, n, policy)]
                        base.require(np.array_equal(ref.movie_ids, movies) and np.array_equal(ref.raw_ratings, raw)
                            and np.allclose(ref.q, qs, atol=1e-12, rtol=0)
                            and np.allclose(ref[list(prior.METRICS)].to_numpy(dtype=float), values, atol=1e-12, rtol=0),
                            "reference per-user metrics drift")
                    rows.append({"user_key": key, "n": n, "policy": policy, "movie_ids": movies,
                        "raw_ratings": list(raw), "q": list(qs), **dict(zip(prior.METRICS, values.tolist()))})
                if k:
                    for p in range(2):
                        differences[u, k - 1, p] = prior.observed_difference(raw_by_policy[2], raw_by_policy[p], hists[u])
        self.guard()
        intervals = base.ci_bounds(differences, self.cfg["bootstrap_repeats"], self.cfg["bootstrap_seed"], self.cfg["alpha"], self.cfg["family_size"])
        comparisons = []
        for k, n in enumerate(self.cfg["ns"][1:]):
            for p, reference in enumerate(self.cfg["policies"][:2]):
                for j, metric in enumerate(prior.METRICS):
                    lo, hi = intervals[k, p, j].tolist()
                    comparisons.append({"n": n, "reference": reference, "metric": metric,
                        "difference_ALS_minus_reference": float(differences[:, k, p, j, 0].mean()),
                        "simultaneous_exploratory_ci": [lo, hi], "ci_width": hi - lo, "status": prior.direction(metric, lo, hi)})
        frame = pd.DataFrame(rows)
        aggregate = [{"n": int(n), "policy": policy, "users": len(group), "slots": 2 * len(group), "label_coverage": 1.,
            "metrics": {m: float(group[m].mean()) for m in prior.METRICS}}
            for (n, policy), group in frame.groupby(["n", "policy"], sort=True)]
        frame.to_parquet(self.root / "user-metrics.parquet", index=False)
        base.write_json(self.root / "metrics.json", {"amendment": self.cfg["amendment"], "users": len(self.keys),
            "unique_targets": len(self.target_ids), "claim_scope": "EXPLORATORY_FIXED_CHECKPOINT_WITHIN_OBSERVED_E",
            "metrics": aggregate, "paired_differences": comparisons, "family_size": 18,
            "fixed_H_Q_verified": True, "reference_per_user_metrics_match": True, "training_runs": 0,
            "als_only_score_runs": 1, "new_content_or_hybrid_score_runs": 0, "extension_labels_used": False,
            "new_confirmation": False, "learning_convergence_verified": False, "product_adopted": False, "final_K_selected": False})

    def run(self):
        self.sources()
        complete = self.root / "completion-seal.json"
        if complete.exists():
            validate_completion(self.root, self.identity, base.pin(self.paths["conditional_completion-seal.json"]))
            self.validate_score()
            print("VERIFIED_EXISTING_COMPLETION_NO_RESCORING")
            return
        base.require(not self.root.exists() or not any(self.root.iterdir()), "partial run exists; preserve without retry")
        self.root.mkdir(parents=True, exist_ok=True)
        self.budget = prior.Budget(self.root, self.identity, self.cfg)
        self.budget.save()
        self.budget.thread.start()
        try:
            self.prepare()
            self.score()
            self.evaluate()
            self.sources()
            self.guard()
            self.budget.close()
            self.guard()
            base.write_json(complete, {"status": "COMPLETE", "fingerprint": self.identity,
                "source_completion": base.pin(self.paths["conditional_completion-seal.json"]),
                "outputs": {name: base.pin(self.root / name) for name in OUTPUTS},
                "seconds": self.budget.elapsed(), "peak_rss_bytes": self.budget.peak,
                "original_files_preserved": True, "new_raw_or_protected_labels_opened": False})
            print({"phase": "ALS_ONLY_COMPLETE", "seconds": round(self.budget.elapsed(), 3)}, flush=True)
        except Exception as exc:
            self.budget.close()
            if not (self.root / "failure.json").exists():
                base.write_json(self.root / "failure.json", {"status": "FAILED", "fingerprint": self.identity,
                    "error_type": type(exc).__name__, "error": str(exc)})
            raise


if __name__ == "__main__":
    Run().run()
