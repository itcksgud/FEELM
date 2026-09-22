"""Label-blind public/evidence strata, full strict-past inputs, no reserved users."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import build_per_target_rating_pilot_v1 as base
from strict_evidence_v12 import DirectIndex, metadata, public_references, DIRECT_NAMES
from public_evidence_v2 import PublicCalibration
from train_pointwise_rating_pilot_v1 import digest


def build(temporal: Path, user_index: Path, bridge: Path, catalog: Path, old_dataset: Path,
          reserved: Path, shared: Path, output: Path, train_users: int = 30000, cap: int = 6):
    if output.exists() or shared.exists():
        raise FileExistsError("v12 artifact path already exists")
    old = json.loads((old_dataset / "manifest.json").read_text(encoding="utf-8"))
    for key, path in (("temporal_examples", temporal), ("user_index", user_index), ("metadata", bridge), ("catalog", catalog)):
        if digest(path) != old["sources"][key]["sha256"]:
            raise ValueError(f"source mismatch {key}")
    reserve_manifest = json.loads((reserved.parent / "manifest.json").read_text(encoding="utf-8"))
    if digest(reserved) != reserve_manifest["reserved_users_sha256"]:
        raise ValueError("reservation checksum mismatch")
    index = pd.read_parquet(user_index)
    previous = pd.read_parquet(old_dataset / "rows.parquet", columns=["uid", "split"])
    train_ids = sorted(index.loc[index.split_id.eq(0), "uid"].astype(int),
                       key=lambda uid: base._hash(f"strict-v12-train:{uid}"))[:train_users]
    valid_ids = sorted(previous.loc[previous.split.eq("valid"), "uid"].unique().astype(int))
    if train_users < 1000:
        valid_ids = valid_ids[:max(10, train_users // 5)]
    allowed = set(train_ids) | set(valid_ids)
    reserved_ids = set(pd.read_parquet(reserved).uid.astype(int))
    if allowed & reserved_ids or set(train_ids) & set(valid_ids):
        raise ValueError("reserved or train/valid user overlap")
    columns = ["uid", "movie_id", "rating", "timestamp", "history_end_exclusive", "split_id"]
    # Predicate is applied before the returned table is materialized. This does
    # not claim parquet storage pages never contain other users' encoded values.
    events = pd.read_parquet(temporal, columns=columns, filters=[("uid", "in", sorted(allowed))])
    if not set(events.uid.astype(int)).issubset(allowed) or events.split_id.eq(2).any():
        raise ValueError("filtered source leaked an excluded user")
    if events.duplicated(["uid", "movie_id"]).any():
        raise ValueError("rating revisions require explicit handling")
    if not set(events.uid).issubset(set(index.uid)):
        raise ValueError("unknown event user")
    shared.mkdir(parents=True)
    events.to_parquet(shared / "selected_events.parquet", index=False)
    chosen_index = index.loc[index.uid.isin(allowed)].copy()
    chosen_index.to_parquet(shared / "selected_user_index.parquet", index=False)
    catalog_frame = pd.read_parquet(catalog)
    calibration = PublicCalibration.from_catalog(catalog_frame)
    refs = public_references(catalog_frame, calibration.reference_vote_quantile)
    refs.to_parquet(shared / "public_references.parquet", index=False)
    bridge_frame = pd.read_parquet(bridge).sort_values("movie_id", kind="stable")
    movies = metadata(bridge_frame)
    movie_public = bridge_frame[["movie_id", "tmdb_id"]].merge(refs, on="tmdb_id", validate="one_to_one").set_index("movie_id")
    if len(movie_public) != len(bridge_frame):
        raise ValueError("bridge/public axis mismatch")
    public_lookup = {int(row.movie_id): row for row in movie_public.reset_index().itertuples(index=False)}
    train_set = set(train_ids)
    chosen_extra = {}
    extra_rows = []
    selection_stats = defaultdict(lambda: {"eligible": 0, "selected": 0})
    original_selector, original_flush = base._selected_positions, base._flush

    def choose(user, lookup, limit):
        uid = int(user.uid.iloc[0])
        eligible, bad_year, eligible_count = original_selector(user, lookup, 0)
        movie = user.movie_id.to_numpy(np.int64)
        stars = user.rating.to_numpy(np.float32)
        pointers = user.history_end_exclusive.to_numpy(np.int64)
        direct_index = DirectIndex(movies)
        cursor = 0
        strata = defaultdict(list)
        direct_rows = {}
        for position in eligible:
            while cursor < pointers[position]:
                direct_index.add(int(movie[cursor]), float(stars[cursor]))
                cursor += 1
            features, _ = direct_index.query(movies[int(movie[position])])
            direct_rows[int(position)] = features
            public = public_lookup[int(movie[position])]
            tmdb_strong = public.votes >= calibration.reference_vote_quantile
            kobis_strong = pd.notna(public.d_kobis) and public.d_kobis == 0
            missing = public.votes == 0 and pd.isna(public.d_kobis)
            route = "missing" if missing else f"tmdb{int(tmdb_strong)}_kobis{int(kobis_strong)}"
            key = (route, min(2, int(features[5])))
            strata[key].append(int(position))
        selected = []
        rng = np.random.default_rng(base._hash(f"strict-v12-targets:{uid}"))
        if uid in train_set:
            for key, positions in sorted(strata.items()):
                count = min(cap, len(positions))
                take = np.sort(rng.choice(positions, size=count, replace=False)).astype(int)
                probability = count / len(positions)
                selected.extend(take.tolist())
                selection_stats[str(key)]["eligible"] += len(positions)
                selection_stats[str(key)]["selected"] += count
                for pos in take:
                    chosen_extra[(uid, int(movie[pos]))] = (direct_rows[int(pos)], probability)
        else:
            take = base._choose_targets(uid, eligible, 20)
            probability = len(take) / max(len(eligible), 1)
            selected = take.tolist()
            for pos in take:
                chosen_extra[(uid, int(movie[pos]))] = (direct_rows[int(pos)], probability)
        return np.asarray(sorted(selected), np.int64), bad_year, eligible_count

    def flush(writer, buffer, schema):
        for row in buffer:
            direct, probability = chosen_extra.pop((int(row["uid"]), int(row["movie_id"])))
            row["sampling_probability"] = probability
            extra_rows.append(direct)
        return original_flush(writer, buffer, schema)

    del events
    with patch.object(base, "_selected_positions", choose), patch.object(base, "_flush", flush):
        report = base.build(shared / "selected_events.parquet", shared / "selected_user_index.parquet",
                            bridge, catalog, output, max_users=len(allowed), targets_per_user=20)
    direct = np.asarray(extra_rows, np.float32)
    if direct.shape != (report["counts"]["selected_targets"], len(DIRECT_NAMES)) or chosen_extra:
        raise ValueError("direct row alignment failed")
    np.save(output / "strict_direct.npy", direct, allow_pickle=False)
    rows = pd.read_parquet(output / "rows.parquet", columns=["uid", "split", "rating", "sampling_probability", "movie_id"])
    if rows.split.eq("test").any() or set(rows.uid.astype(int)) & reserved_ids:
        raise ValueError("heldout user materialized in training artifact")
    weight = 1 / rows.loc[rows.split.eq("train"), "sampling_probability"].to_numpy(float)
    report["settings"].update({"user_selection": "30k split0 hash users plus existing split1 development users; no split2",
        "target_selection": "train: label-blind public two-bit/missing x Q+(0/1/2+) strata, up to cap each; valid: uniform up to20",
        "sampling_probability": "per-user stratum inclusion only; inverse probability train weighting, user inclusion is constant within split"})
    target_public = bridge_frame[["movie_id", "tmdb_id"]].merge(refs, on="tmdb_id", validate="one_to_one").set_index("movie_id").loc[rows.movie_id]
    audit = pd.DataFrame({"split": rows.split.to_numpy(), "movie_id": rows.movie_id.to_numpy(),
        "uid": rows.uid.to_numpy(), "low_public": target_public.required.to_numpy() > 0,
        "positive_direct_ge2": direct[:, 5] >= 2, "actual_positive": rows.rating.to_numpy() >= 4,
        "actual_negative": rows.rating.to_numpy() <= 2.5})
    support_audit = audit.groupby(["split", "low_public", "positive_direct_ge2"]).agg(
        rows=("movie_id", "size"), films=("movie_id", "nunique"), users=("uid", "nunique"),
        actual_positive=("actual_positive", "sum"), actual_negative=("actual_negative", "sum")).reset_index().to_dict("records")
    report.update({"variant": "v12-public-route-direct-stratified-observed", "original_sources": old["sources"],
        "strict_feature_names": DIRECT_NAMES, "strict_direct_sha256": digest(output / "strict_direct.npy"),
        "public_references": {"path": str(shared / "public_references.parquet"), "sha256": digest(shared / "public_references.parquet")},
        "public_reference_votes": calibration.reference_vote_quantile,
        "reservation_sha256": digest(reserved), "reserved_user_overlap": 0,
        "stratified_cap": cap, "strata": dict(selection_stats),
        "observed_target_support": support_audit,
        "sampling_weight": {"method": "inverse per-user stratum target probability; train mean-normalized, no clipping",
            "min": float(weight.min()), "max": float(weight.max()), "mean": float(weight.mean()),
            "ess": float(weight.sum() ** 2 / np.sum(weight ** 2))},
        "valid_scope": "previously viewed split1 users; uniform up-to-20 observed targets per user; no new blind claim",
        "new_holdout_labels_used": False, "builder_v12_sha256": digest(Path(__file__))})
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"counts": report["counts"], "weight": report["sampling_weight"]}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("temporal", "user-index", "bridge", "catalog", "old-dataset", "reserved", "shared", "output"):
        parser.add_argument(f"--{arg}", type=Path, required=True)
    parser.add_argument("--train-users", type=int, default=30000)
    parser.add_argument("--cap", type=int, default=6)
    a = parser.parse_args()
    build(a.temporal, a.user_index, a.bridge, a.catalog, a.old_dataset, a.reserved, a.shared, a.output, a.train_users, a.cap)
