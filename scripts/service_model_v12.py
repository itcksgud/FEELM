"""Local personal-scoring adapter; deliberately NOT a Redis/ALS artifact adapter."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def movie_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= np.iinfo(np.int64).max:
        raise ValueError("movieId must be a positive service integer ID")
    return value


def merge_inputs(request):
    """No title/language filter, no K cap, no recency weighting, no DB mutation."""
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    allowed = {"ratings", "dismissedMovieIds", "watchedMovieIds"}
    if set(request) - allowed:
        raise ValueError(f"unknown request fields: {sorted(set(request) - allowed)}")
    if any(not isinstance(request.get(name, []), list) for name in allowed):
        raise ValueError("input collections must be lists")
    actual = []
    seen = set()
    for row in request.get("ratings", []):
        if not isinstance(row, dict) or set(row) != {"movieId", "score"}:
            raise ValueError("rating must contain exactly movieId and score")
        mid = movie_id(row["movieId"])
        score = row["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("score must be numeric")
        score = float(score)
        if not math.isfinite(score) or not .5 <= score <= 5 or score * 2 != int(score * 2):
            raise ValueError("score must be finite 0.5-step in [0.5,5]")
        if mid in seen:
            raise ValueError("duplicate actual rating; resolve current DB snapshot first")
        seen.add(mid)
        actual.append({"movieId": mid, "score": score, "source": "RATING"})
    dismissed = {movie_id(mid) for mid in request.get("dismissedMovieIds", [])}
    watched = {movie_id(mid) for mid in request.get("watchedMovieIds", [])}
    history = actual + [{"movieId": mid, "score": 1., "source": "DISMISS"}
                        for mid in sorted(dismissed - seen)]
    return history, seen | dismissed | watched


def verify_bundle(root):
    root = Path(root).resolve(strict=True)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["format"] != "feelm-personal-v12-local-v1" or manifest["adoption"] != "NOT_ADOPTED":
        raise ValueError("unsupported local bundle contract")
    actual_files = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual_files != set(manifest["files"]) | {"manifest.json"}:
        raise ValueError("bundle contains missing/unlisted files (including Python bytecode)")
    for relative, record in manifest["files"].items():
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise ValueError(f"bundle integrity failure: {relative}")
    return root, manifest


@contextmanager
def no_bytecode():
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        sys.dont_write_bytecode = previous


class PersonalScorer:
    def __init__(self, root, family="both"):
        if family not in ("gbt", "fm", "both"):
            raise ValueError("family must be gbt, fm or both")
        self.root, self.manifest = verify_bundle(root)
        # The generated modules contain exact source symbols, not training CLIs.
        vendor_parent = str(self.root)
        for name, module in list(sys.modules.items()):
            if name == "feelm_v12_vendor" or name.startswith("feelm_v12_vendor."):
                origin = Path(module.__file__).resolve()
                if not origin.is_relative_to(self.root):
                    raise ValueError("use a separate process for a different frozen bundle")
        if vendor_parent not in sys.path:
            sys.path.insert(0, vendor_parent)
        with no_bytecode():
            from feelm_v12_vendor.build_evidence_rank_dataset_v1 import _metadata_lookup, _history_counters, _evidence
            from feelm_v12_vendor.strict_evidence_v12 import metadata, DirectIndex, augment, qualifications
            from feelm_v12_vendor.public_evidence_v2 import PublicCalibration
            from feelm_v12_vendor.public_evidence_v4 import numeric_features_v4
            from feelm_v12_vendor.probe_per_target_public_v2 import _history_vote_context
            from feelm_v12_vendor.rating_semantics_v7 import utility
        self.features = (_history_counters, _evidence, DirectIndex, augment, qualifications,
                         numeric_features_v4, _history_vote_context, utility)
        self.catalog = pd.read_parquet(self.root / "catalog.parquet")
        self.ids = self.catalog.movie_id.to_numpy(np.int64)
        if (not self.catalog.movie_id.equals(self.catalog.service_movie_id)
                or self.catalog.movie_id.duplicated().any() or self.catalog.tmdb_id.duplicated().any()
                or not np.all(self.ids[:-1] < self.ids[1:])):
            raise ValueError("service/TMDB catalog axis mismatch")
        if sha256(self.root / "feature-schema.json") != self.manifest["feature_schema_sha256"]:
            raise ValueError("feature schema pin mismatch")
        self.names = json.loads((self.root / "feature-schema.json").read_text())
        self.report = json.loads((self.root / "training-metrics.json").read_text())
        if self.names != self.report["features"]:
            raise ValueError("ordered training feature mismatch")
        self.refs = pd.read_parquet(self.root / "public-references.parquet")
        if not self.refs.tmdb_id.equals(self.catalog.tmdb_id):
            raise ValueError("public reference identity mismatch")
        self.movies = metadata(self.catalog)
        self.lookup = _metadata_lookup(self.catalog, self.catalog)
        self.calibration = PublicCalibration.from_dict(self.report["calibration"])
        self.cohort_axis = np.load(self.root / "cohort-axis.npy", allow_pickle=False)
        self.cohort = np.load(self.root / "cohort-support.npy", allow_pickle=False)
        self.families = ("gbt", "fm") if family == "both" else (family,)
        if "gbt" in self.families:
            import xgboost as xgb
            self.xgb = xgb
            self.booster = xgb.Booster()
            self.booster.load_model(self.root / "gbt-per-target.json")
            self.booster.set_param({"nthread": 6})
            if self.booster.num_features() != len(self.names):
                raise ValueError("GBT feature count mismatch")
        if "fm" in self.families:
            with np.load(self.root / "fm-per-target.npz", allow_pickle=False) as state:
                if list(state["feature_names"]) != self.names or str(state["movie_axis_sha256"]) != self.report["movie_axis_sha256"]:
                    raise ValueError("FM training schema/axis mismatch")
                self.linear = state["linear"].copy()
                self.bias = float(state["bias"][0])
                width = state["factor"].shape[1]
            self.item_factors = np.load(self.root / "fm-item-factors.npy", allow_pickle=False)
            if self.item_factors.shape != (len(self.ids), width) or not np.isfinite(self.item_factors).all():
                raise ValueError("FM precomputed content factors invalid")

    def recommend(self, request, limit=500, chunk_size=5000):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer in [1,500]")
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        started = time.monotonic()
        history, excluded = merge_inputs(request)
        ids = np.asarray([row["movieId"] for row in history], np.int64)
        positions = np.searchsorted(self.ids, ids)
        if (positions >= len(self.ids)).any() or not np.array_equal(self.ids[positions], ids):
            unknown = sorted(set(ids.tolist()) - set(self.ids.tolist()))
            raise ValueError(f"input has no verified catalog metadata; no silent drop: {unknown}")
        response = {"format": self.manifest["format"], "adoption": "NOT_ADOPTED",
            "scope": "PERSONAL_SCORER_ONLY", "inputCount": len(history), "input": history,
            "actualRatingCount": sum(row["source"] == "RATING" for row in history),
            "syntheticDismissCount": sum(row["source"] == "DISMISS" for row in history),
            "policyVersion": self.manifest["policy_version"], "featureVersion": self.manifest["feature_version"],
            "models": {}, "status": "OK" if history else "NEEDS_HOST_COLD_START"}
        if not history:
            response["models"] = {family: {"candidates": [], "reason": "NO_PREFERENCE_INPUT"} for family in self.families}
            return response
        counters, evidence, direct_class, augment, qualify, numeric, vote_context, utility = self.features
        stars = np.asarray([row["score"] for row in history], np.float32)
        counts = counters(list(zip(ids.tolist(), stars.tolist())), self.lookup)
        direct_index = direct_class(self.movies)
        for mid, score in zip(ids, stars):
            direct_index.add(int(mid), float(score))
        context = vote_context(stars, self.catalog.iloc[positions].tmdb_vote_count.to_numpy(np.float32))
        style = [float(stars.mean()), float(stars.std()), float((stars >= 3).mean()), float((stars <= 2.5).mean())]
        if "fm" in self.families:
            weight = utility(stars)
            weight /= max(float(np.abs(weight).sum()), 1.)
            user_factor = np.asarray(self.item_factors[positions].T @ weight).reshape(-1)
        current = np.flatnonzero(self.refs.eligible_movie.to_numpy(bool) & ~np.isin(self.ids, list(excluded)))
        scores = {family: np.empty(len(current), np.float32) for family in self.families}
        direct = np.empty((len(current), 14), np.float32)
        for start in range(0, len(current), chunk_size):
            stop = min(start + chunk_size, len(current))
            chunk = current[start:stop]
            frame = pd.DataFrame([evidence(self.lookup[int(self.ids[pos])], counts) for pos in chunk])
            frame["tmdb_id"] = self.catalog.iloc[chunk].tmdb_id.to_numpy(np.int64)
            frame["k"] = len(stars)
            for name, value in zip(("history_rating_mean", "history_rating_std", "history_positive_share", "history_negative_share"), style):
                frame[name] = value
            base, names = numeric(frame, self.calibration,
                vote_context={name: np.full(len(chunk), value, np.float32) for name, value in context.items()},
                tmdb_axis=self.cohort_axis, cohort_support=self.cohort)
            for offset, pos in enumerate(chunk):
                direct[start + offset], _ = direct_index.query(self.movies[int(self.ids[pos])])
            matrix, names = augment(base, names, direct[start:stop], self.refs.iloc[chunk])
            if names != self.names:
                raise ValueError("training/serving ordered feature mismatch")
            if "gbt" in scores:
                scores["gbt"][start:stop] = self.booster.predict(self.xgb.DMatrix(matrix))
            if "fm" in scores:
                scores["fm"][start:stop] = self.bias + matrix @ self.linear + self.item_factors[chunk] @ user_factor
        required = self.refs.iloc[current].required.to_numpy(np.int32)
        qualified = qualify(direct, required)
        for family, values in scores.items():
            if not np.isfinite(values).all():
                raise ValueError("nonfinite inference")
            order = np.lexsort((self.ids[current], -values))
            raw_rank = np.empty(len(current), np.int64)
            raw_rank[order] = np.arange(1, len(current) + 1)
            chosen = order[qualified[order]][:limit]
            rows = []
            for rank, rel in enumerate(chosen, 1):
                pos = current[rel]
                mid = int(self.ids[pos])
                _, detail = direct_index.query(self.movies[mid], explain=True)
                rows.append({"rank": rank, "movieId": mid, "score": float(values[rel]),
                    "rawRank": int(raw_rank[rel]), "reasons": [],
                    "diagnostics": {"required": int(required[rel]), "positive": int(direct[rel, 5]),
                        "negative": int(direct[rel, 12]), "evidence": detail}})
            response["models"][family] = {"modelVersion": f"strict-v12-{family}-local-20260922",
                "candidates": rows, "qualifiedCount": int(qualified.sum()), "candidateCount": len(current),
                "shortfall": max(0, limit - len(rows)),
                "rawTop100": [{"movieId": int(self.ids[current[rel]]), "score": float(values[rel]),
                               "qualified": bool(qualified[rel])} for rel in order[:100]]}
        response["seconds"] = time.monotonic() - started
        return response


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=("gbt", "fm", "both"), default="both")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.output.resolve().is_relative_to(args.bundle.resolve()):
        raise ValueError("write results outside the immutable bundle")
    request = json.loads(args.request.read_text(encoding="utf-8-sig"))
    result = PersonalScorer(args.bundle, args.model).recommend(request, args.limit)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({"status": result["status"], "inputCount": result["inputCount"], "models": list(result["models"])}))
