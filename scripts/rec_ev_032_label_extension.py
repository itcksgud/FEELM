"""Complete labels for the same sealed REC032 recommendations; never re-rank."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import time
import zipfile
import numpy as np
import pandas as pd
import rec_ev_032_basic as base

ROOT = base.ROOT
PLAN = ROOT / "docs/recommendation/experiments/rec-ev-032/label-extension"
FILES = {"design": PLAN / "README.md", "config": PLAN / "config.json",
         "runner": Path(__file__).resolve(),
         "tests": ROOT / "scripts/tests/test_rec_ev_032_label_extension.py",
         "base_runner": ROOT / "scripts/rec_ev_032_basic.py"}
METRICS = ("MEAN_Q", "MIN_Q", "HARM20")


def fingerprint():
    return {k: base.sha(p) for k, p in FILES.items()}


def check_review():
    base.check_review()
    actual = fingerprint()
    review = base.read_json(PLAN / "review.json")
    base.require(review["status"] == "PASS" and review["fingerprint"] == actual, "extension review absent or stale")
    return actual


def selected_user(uid):
    return (0 < uid <= 200948
        and base.bucket(uid, "feelm-rec-vnext-user-split-v1|", 100, True) <= 59
        and base.bucket(uid, "rec-ev-022a-user-role-v1|", 10000) <= 5999
        and base.bucket(uid, "rec-ev-028-user-phase-v1|", 10000) <= 7999
        and 8000 <= base.bucket(uid, "rec-ev-029-direct-user-split-v1|", 10000) <= 8999)


def read_pair(line, raw_to_key, wanted, max_user, max_movie):
    first = line.find(b",")
    base.require(first > 0, "invalid user field")
    uid = int(line[:first])
    base.require(0 < uid <= max_user, "user ID outside frozen archive")
    key = raw_to_key.get(uid)
    if key is None:
        return None, None, None, "OTHER_USER"
    second = line.find(b",", first + 1)
    base.require(second > first, "invalid movie field")
    movie = int(line[first + 1:second])
    base.require(0 < movie <= max_movie, "invalid movie ID")
    if movie not in wanted[key]:
        return key, movie, None, "OUTSIDE_REQUEST"
    third = line.find(b",", second + 1)
    base.require(third > second, "invalid requested rating field")
    return key, movie, base.rating_index(line[second + 1:third]), "REQUESTED"


def requests_for(ranking, profile30):
    base.require(ranking.shape == (4, 2, 2), "frozen ranking shape")
    return set(map(int, ranking.ravel())) - set(map(int, profile30))


def add_label(found, key, movie, index, hist, old):
    pair = (key, movie)
    base.require(pair not in found, "duplicate requested user/movie")
    raw, q = (index + 1) / 2, base.q_from_hist(index, hist)
    if pair in old:
        base.require(old[pair][0] == raw and abs(old[pair][1] - q) < 1e-12, "old label mismatch")
    found[pair] = (raw, q)


def clean(value):
    return None if pd.isna(value) else float(value)


def validate_old_found(old, found):
    base.require(set(old) <= set(found), "old observed label missing from archive")
    for pair, value in old.items():
        base.require(found[pair][0] == value[0] and abs(found[pair][1] - value[1]) < 1e-12, "old outcome changed")


def category(movie, profile, old, found, key):
    if movie in profile:
        return "INPUT_RESERVED"
    if (key, movie) in old:
        return "OLD_LABEL"
    return "NEW_LABEL" if (key, movie) in found else "NO_RATING_IN_ARCHIVE"


class Run:
    def __init__(self):
        self.identity = check_review()
        self.cfg = base.read_json(FILES["config"])
        self.original_cfg = base.read_json(base.FILES["config"])
        c = self.cfg
        base.require(c["ns"] == self.original_cfg["ns"] == [0, 5, 10, 30]
                     and c["policies"] == self.original_cfg["policies"]
                     and c["bootstrap_repeats"] == 5000 and c["bootstrap_seed"] == 20260907
                     and c["family_size"] == 9 and c["alpha"] == .05, "analysis changed")
        self.root = ROOT / c["output_root"]
        base.require(self.root.resolve() == (ROOT / "outputs/recommendation-evidence/rec-ev-032/label-extension").resolve(),
                     "unexpected output location")
        self.start = time.monotonic()
        self.paths = {}

    def guard(self):
        base.require(check_review() == self.identity, "execution identity changed")
        base.require(time.monotonic() - self.start <= self.cfg["timeout_seconds"], "extension execution timeout")

    def sources(self):
        self.guard()
        paths = {}
        for name, spec in self.cfg["inputs"].items():
            p = Path(spec["path"])
            if not p.is_absolute():
                p = ROOT / p
            base.require(base.pin(p) == {"bytes": spec["bytes"], "sha256": spec["sha256"]},
                         "source drift: " + name)
            paths[name] = p
        identity = base.fingerprint()
        seals = {name: base.read_json(paths[name + "_seal"]) for name in ("prepare", "train", "score", "evaluate")}
        for seal in seals.values():
            base.require(seal["status"] == "COMPLETE" and seal["fingerprint"] == identity, "original identity drift")
        base.require(seals["train"]["prepare_seal"] == base.pin(paths["prepare_seal"])
            and seals["score"]["prepare_seal"] == base.pin(paths["prepare_seal"])
            and seals["score"]["train_seal"] == base.pin(paths["train_seal"])
            and seals["evaluate"]["score_seal"] == base.pin(paths["score_seal"]), "original dependency drift")
        for key, phase, name in [("profiles", "prepare", "profiles.parquet"), ("rankings", "score", "rankings.npz"),
                               ("old_user_metrics", "evaluate", "user-metrics.parquet"),
                               ("old_metrics", "evaluate", "metrics.json")]:
            base.require(base.pin(paths[key]) == seals[phase]["outputs"][name], "original output/seal mismatch")
        base.require(self.cfg["inputs"]["archive"] == self.original_cfg["inputs"]["archive"]
            and self.cfg["inputs"]["reference_histograms"] == self.original_cfg["inputs"]["labels"],
            "archive/H source changed")
        self.paths = paths

    def load_request(self):
        with np.load(self.paths["rankings"], allow_pickle=False) as z:
            self.keys, self.ranks, ns = z["user_keys"], z["movie_ids"], z["ns"]
        base.require(self.ranks.shape == (self.cfg["expected_users"], 4, 2, 2)
            and len(set(self.keys)) == self.cfg["expected_users"] and ns.tolist() == self.cfg["ns"],
            "ranking cohort/grid changed")
        frame = pd.read_parquet(self.paths["profiles"], columns=["user_key", "profile_movie_ids"])
        base.require(len(frame) == len(self.keys) and not frame.user_key.duplicated().any()
                     and set(frame.user_key) == set(self.keys), "profile cohort mismatch")
        self.profiles = {r.user_key: set(map(int, r.profile_movie_ids)) for r in frame.itertuples(index=False)}
        base.require(all(len(p) == 30 for p in self.profiles.values()), "profile30 identity")
        self.wanted = {key: requests_for(self.ranks[u], self.profiles[key]) for u, key in enumerate(self.keys)}
        reference = pd.read_parquet(self.paths["reference_histograms"],
            columns=["user_key", "full_history_histogram", "full_history_count"],
            filters=[("outer", "==", "R0"), ("user_key", "in", self.keys.tolist())])
        base.require(len(reference) == len(self.keys) and not reference.user_key.duplicated().any()
                     and set(reference.user_key) == set(self.keys), "fixed H coverage")
        self.hist = {}
        for row in reference.itertuples(index=False):
            hist = np.asarray(row.full_history_histogram, dtype=np.int64)
            base.require(hist.shape == (10,) and (hist >= 0).all()
                         and hist.sum() == row.full_history_count and hist.sum() > 0, "fixed H invalid")
            self.hist[row.user_key] = hist
        self.old = {}
        old_frame = pd.read_parquet(self.paths["old_user_metrics"],
            columns=["user_key", "n", "policy", "movie_ids", "q", "raw_ratings"])
        lookup = {key: u for u, key in enumerate(self.keys)}
        base.require(len(old_frame) == len(self.keys) * 8
            and not old_frame.duplicated(["user_key", "n", "policy"]).any(), "old output coverage")
        for row in old_frame.itertuples(index=False):
            base.require(row.user_key in lookup and row.n in self.cfg["ns"] and row.policy in self.cfg["policies"],
                         "old output role")
            u, n, p = lookup[row.user_key], self.cfg["ns"].index(row.n), self.cfg["policies"].index(row.policy)
            base.require(list(row.movie_ids) == self.ranks[u, n, p].tolist(), "old recommendation identity")
            base.require(len(row.movie_ids) == len(row.q) == len(row.raw_ratings) == 2, "old slots")
            for movie, q, raw in zip(row.movie_ids, row.q, row.raw_ratings, strict=True):
                movie, q, raw = int(movie), clean(q), clean(raw)
                base.require((q is None) == (raw is None), "partial old label")
                if q is not None:
                    pair = (row.user_key, movie)
                    base.require(movie in self.wanted[row.user_key], "old label overlaps O30")
                    fixed = base.q_from_hist(base.rating_index(raw), self.hist[row.user_key])
                    base.require(abs(q - fixed) < 1e-12 and (pair not in self.old or self.old[pair] == (raw, q)),
                                 "old Q/H or duplicate mismatch")
                    self.old[pair] = (raw, q)
        keys = set(self.keys)
        self.raw_to_key = {}
        for uid in range(1, self.cfg["max_user_id"] + 1):
            key = base.user_key(uid)
            if key in keys:
                base.require(selected_user(uid), "user outside original selection role")
                self.raw_to_key[uid] = key
        base.require(len(self.raw_to_key) == len(keys), "user key mapping coverage")

    def request_seal(self):
        rows = [{"user_key": key, "movie_id": movie} for key in self.keys for movie in sorted(self.wanted[key])]
        pd.DataFrame(rows).to_parquet(self.root / "requests.parquet", index=False)
        np.savez_compressed(self.root / "histograms.npz", user_keys=self.keys,
                            histograms=np.stack([self.hist[key] for key in self.keys]))
        self.guard()
        base.write_json(self.root / "request-seal.json", {
            "status": "SEALED_BEFORE_NEW_RATING_LOOKUP", "fingerprint": self.identity,
            "original_rankings": base.pin(self.paths["rankings"]), "users": len(self.keys),
            "requested_pairs": len(rows), "old_observed_pairs": len(self.old),
            "outputs": {name: base.pin(self.root / name) for name in ("requests.parquet", "histograms.npz")}})

    def scan(self):
        self.guard()
        counters = {"raw_rows": 0, "OTHER_USER": 0, "OUTSIDE_REQUEST": 0, "REQUESTED": 0,
                    "timestamps_parsed": 0, "nonrequested_rating_values_parsed": 0}
        counts = {key: 0 for key in self.keys}
        found = {}
        with zipfile.ZipFile(self.paths["archive"]) as z, z.open("ml-32m/ratings.csv") as source:
            base.require(source.readline().rstrip(b"\r\n") == b"userId,movieId,rating,timestamp", "raw schema changed")
            for line in source:
                counters["raw_rows"] += 1
                key, movie, index, status = read_pair(line, self.raw_to_key, self.wanted,
                    self.cfg["max_user_id"], self.cfg["max_movie_id"])
                counters[status] += 1
                if key is not None:
                    counts[key] += 1
                if status == "REQUESTED":
                    add_label(found, key, movie, index, self.hist[key], self.old)
                if counters["raw_rows"] % 4000000 == 0:
                    self.guard()
                    print(json.dumps({"phase": "PAIR_LOOKUP", "rows": counters["raw_rows"],
                                      "ratings_parsed": counters["REQUESTED"]}), flush=True)
        base.require(counters["raw_rows"] == self.cfg["expected_raw_rows"], "incomplete archive scan")
        base.require(all(counts[key] == int(self.hist[key].sum()) for key in self.keys), "H/source user row-count mismatch")
        validate_old_found(self.old, found)
        return found, counters

    def evaluate(self, found, counters):
        rows = []; difference = np.zeros((len(self.keys), 3, 3, 2))
        old_maps, maps = {}, {}
        for (key, movie), (_, q) in found.items():
            maps.setdefault(key, {})[movie] = q
        for (key, movie), (_, q) in self.old.items():
            old_maps.setdefault(key, {})[movie] = q
        for u, key in enumerate(self.keys):
            for k, n in enumerate(self.cfg["ns"]):
                movies_p, movies_m = self.ranks[u, k]
                if k:
                    extended = base.paired_bounds(movies_m.tolist(), movies_p.tolist(), maps.get(key, {}))
                    old = base.paired_bounds(movies_m.tolist(), movies_p.tolist(), old_maps.get(key, {}))
                    base.require(np.all(extended[:, 0] >= old[:, 0] - 1e-12)
                                 and np.all(extended[:, 1] <= old[:, 1] + 1e-12), "label bounds expanded")
                    difference[u, k - 1] = extended
                for p, policy in enumerate(self.cfg["policies"]):
                    movie_ids = self.ranks[u, k, p].tolist()
                    kinds = [category(i, self.profiles[key], self.old, found, key) for i in movie_ids]
                    q = [found[(key, i)][1] if (key, i) in found else None for i in movie_ids]
                    raw = [found[(key, i)][0] if (key, i) in found else None for i in movie_ids]
                    base.require(all(q[j] is None for j, s in enumerate(kinds) if s == "INPUT_RESERVED"),
                                 "O30 entered evaluation")
                    bounds = base.metric_bounds(q)
                    row = {"user_key": key, "n": n, "policy": policy, "movie_ids": movie_ids,
                           "q": q, "raw_ratings": raw, "label_status": kinds, "label_count": sum(v is not None for v in q)}
                    for j, metric in enumerate(METRICS):
                        row[metric + "_lower"], row[metric + "_upper"] = bounds[j].tolist()
                    rows.append(row)
        frame = pd.DataFrame(rows)
        old_result = base.read_json(self.paths["old_metrics"])
        coverage = []
        for (n, policy), group in frame.groupby(["n", "policy"], sort=True):
            kinds = pd.Series([s for row in group.label_status for s in row]).value_counts()
            distribution = group.label_count.value_counts()
            original = next(v for v in old_result["metrics"] if v["n"] == n and v["policy"] == policy)
            old_slots = original["users_by_label_count"]["1"] + 2 * original["users_by_label_count"]["2"]
            base.require(kinds.get("OLD_LABEL", 0) == old_slots, "old coverage changed")
            coverage.append({"n": int(n), "policy": policy, "users": len(group), "slots": 2 * len(group),
                "old_label_slots": old_slots,
                "status_counts": {s: int(kinds.get(s, 0)) for s in
                                  ("OLD_LABEL", "NEW_LABEL", "NO_RATING_IN_ARCHIVE", "INPUT_RESERVED")},
                "label_coverage": float(group.label_count.sum() / (2 * len(group))),
                "users_by_label_count": {str(i): int(distribution.get(i, 0)) for i in (0, 1, 2)},
                "bounds": {m: [float(group[m + "_lower"].mean()), float(group[m + "_upper"].mean())] for m in METRICS}})
        self.guard()
        ci = base.ci_bounds(difference, 5000, 20260907, .05, 9)
        deltas = []
        for k, n in enumerate(self.cfg["ns"][1:]):
            for j, metric in enumerate(METRICS):
                lo, hi = ci[k, j]
                status = "UNDECIDED"
                if lo > 0:
                    status = "WORSE_DIRECTION" if metric == "HARM20" else "BETTER_DIRECTION"
                elif hi < 0:
                    status = "BETTER_DIRECTION" if metric == "HARM20" else "WORSE_DIRECTION"
                deltas.append({"n": n, "metric": metric, "identification_bounds": difference[:, k, j].mean(axis=0).tolist(),
                               "simultaneous_exploratory_ci": [float(lo), float(hi)], "status": status})
        labels = [{"user_key": key, "movie_id": movie, "rating_raw": value[0], "q": value[1],
                   "source": "OLD_LABEL" if (key, movie) in self.old else "NEW_LABEL"}
                  for (key, movie), value in sorted(found.items())]
        self.guard()
        pd.DataFrame(labels).to_parquet(self.root / "matched-labels.parquet", index=False)
        frame.to_parquet(self.root / "user-metrics.parquet", index=False)
        result = {"amendment": self.cfg["amendment"], "users": len(self.keys), "recommendations_changed": False,
            "model_training_runs": 0, "recommendation_generation_runs": 0,
            "requested_unique_pairs": sum(map(len, self.wanted.values())),
            "old_unique_observed_pairs": len(self.old), "found_unique_pairs": len(found),
            "new_unique_observed_pairs": len(found) - len(self.old), "raw_reader": counters,
            "per_user_raw_row_count_matches_fixed_H": True, "old_labels_crosschecked": True,
            "coverage": coverage, "paired_differences": deltas, "new_confirmation": False,
            "product_adopted": False, "final_K_selected": False}
        base.write_json(self.root / "metrics.json", result)

    def run(self):
        self.sources()
        complete = self.root / "completion-seal.json"
        if complete.exists():
            seal = base.read_json(complete)
            base.require(seal["status"] == "COMPLETE" and seal["fingerprint"] == self.identity, "stale completion")
            for name, expected in seal["outputs"].items():
                base.require(base.pin(self.root / name) == expected, "modified completion artifact")
            print("VERIFIED_EXISTING_COMPLETION_NO_NEW_LOOKUP")
            return
        base.require(not self.root.exists() or not any(self.root.iterdir()), "partial extension exists; do not overwrite")
        self.root.mkdir(parents=True, exist_ok=True)
        self.load_request()
        self.request_seal()
        found, counters = self.scan()
        self.evaluate(found, counters)
        self.sources()  # Prove original files/seals still match before completion.
        self.guard()
        names = ["requests.parquet", "histograms.npz", "request-seal.json",
                 "matched-labels.parquet", "user-metrics.parquet", "metrics.json"]
        base.write_json(complete, {"status": "COMPLETE", "fingerprint": self.identity,
            "original_score_seal": base.pin(self.paths["score_seal"]),
            "original_evaluate_seal": base.pin(self.paths["evaluate_seal"]),
            "outputs": {name: base.pin(self.root / name) for name in names},
            "seconds": time.monotonic() - self.start, "raw_reader": counters,
            "original_recommendations_preserved": True, "locked_or_replication_or_reserve_ratings_opened": False})
        print(json.dumps({"phase": "LABEL_EXTENSION_COMPLETE", "seconds": time.monotonic() - self.start,
                          "matched_pairs": len(found), "new_pairs": len(found) - len(self.old)}), flush=True)


if __name__ == "__main__":
    run = Run()
    try:
        run.run()
    except Exception as error:
        if run.root.exists() and not (run.root / "completion-seal.json").exists():
            base.write_json(run.root / "failure.json", {"error_type": type(error).__name__, "error": str(error),
                                                       "fingerprint": run.identity})
        raise

