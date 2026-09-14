"""Bounded, independently reviewed frozen-catalog mutation diagnostic.

Never constructs a predictor, fits a model, or parses future target ratings.
Root inputs are read-only. All new artifacts stay in this isolated worktree.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import pickle
import re
import sys
import threading
import time

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parent / "fixed-k8-discovery-v2-20260913"
DOC = ROOT / "docs/recommendation/experiments/fixed-k8-discovery-v2"
OUT = ROOT / "outputs/fixed-k8-discovery-v2/feelm-discovery-v2-r2"
DEST = HERE / "outputs/mutation-prefix-drift-r2"
CODE = ["dv2_common.py", "dv2_runtime.py", "dv2_predictor.py", "dv2_retrieve.py", "dv2_freeze.py"]
LIMITS = {"users": 20, "scenarios": 3, "seconds": 900, "rss_gib": 8, "minimum_free_gib": 2, "output_mib": 100}


def pin(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(2**20), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "bytes": path.stat().st_size}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False), encoding="utf-8")


def fingerprint():
    cfg = read(DOC / "config.json")
    paths = [Path(__file__), HERE / "MUTATION_PREFIX_DRIFT.md", DOC / "config.json", DOC / "EXECUTION.md"]
    paths += [ROOT / "scripts" / name for name in CODE]
    paths += [Path(cfg["previous_root"]) / "scripts/k8_common.py"]
    return {"stage": "mutation-prefix-drift-r2", "files": {str(p.resolve()): pin(p) for p in sorted(paths)}}


def verify_inventory(stage):
    expected = read(OUT / (stage + "-seal.json"))["files"]
    actual = {str(p.relative_to(OUT)): pin(p) for p in sorted((OUT / stage).rglob("*")) if p.is_file()}
    assert expected == actual, stage + " seal inventory changed"
    return pin(OUT / (stage + "-seal.json"))


def redact_contexts(path):
    # Numeric arrays only; no original target-rating numeric tokens reach json.loads.
    raw = Path(path).read_bytes()
    numeric = rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
    array = rb"\[\s*(?:" + numeric + rb"\s*(?:,\s*" + numeric + rb"\s*)*)?\]"
    redacted, count = re.subn(rb'("ratings"\s*:\s*)' + array, rb"\1null", raw)
    assert count == 1350 and len(re.findall(rb'"ratings"\s*:', raw)) == 1350
    del raw
    parsed = json.loads(redacted)
    assert len(parsed) == 1350 and all(c["ratings"] is None for c in parsed)
    keys = ["uid", "cap", "history", "stars", "original_stars", "original_input_count", "viewed", "raw_pre_count"]
    contexts = [{k: c[k] for k in keys} for c in parsed]
    assert len({(c["uid"], c["cap"]) for c in contexts}) == 1350
    return contexts, count


def material_digest(bundle, array_hash):
    prep = bundle["preprocessor"]
    vec = prep["text_vectorizer"]
    value = {
        "top": array_hash(bundle["top_centers"]),
        "children": [array_hash(c) for c in bundle["child_centers"]],
        "representatives": {k: array_hash(v) for k, v in bundle["representatives"].items()},
        "preprocessor_arrays": {k: array_hash(prep[k]) for k in ["keyword_idf", "keyword_components", "text_components"]},
        "genres": list(prep["genres"]), "keywords": list(prep["keywords"]),
        "text_vocabulary": sorted((k, int(v)) for k, v in vec.vocabulary_.items()),
        "text_idf": array_hash(vec.idf_), "top_weights": bundle["top_weights"],
        "sub_weights": bundle["sub_weights"], "content_weights": bundle["content_weights"],
        "names": bundle["top_names"], "group_names": bundle.get("group_names"),
        "text_parameters": {k: str(v) for k, v in vec.get_params(deep=False).items()},
        "runtime_rules": bundle["runtime_rules"], "quality": bundle["quality"],
        "policy": bundle["policy"], "predictor": bundle["predictor"],
        "candidate_date": bundle["candidate_date"], "runtime_versions": bundle["runtime_versions"],
        "tie_rule": bundle.get("tie_rule"), "source_space_hashes": bundle["source_space_hashes"],
        "review_status": bundle.get("review_status"),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def append_fixture(frame, np):
    added = frame.iloc[:3].copy().reset_index(drop=True)
    first = int(frame.service_movie_id.max()) + 1000
    added["service_movie_id"] = [first, first + 1, first + 2]
    added["tmdb_id"] = [int(frame.tmdb_id.max()) + 1000 + j for j in range(3)]
    added["mapping_status"] = "SERVICE_ONLY_CONTENT_BASED"
    added["movielens_movie_id"] = np.nan
    for row in [1, 2]:
        for field in ["genre_ids", "keyword_ids", "director_ids", "top5_cast_ids", "production_company_ids", "collection_ids", "production_country_codes", "origin_country_codes"]:
            added.at[row, field] = []
        added.at[row, "overview"] = ""
    added.at[1, "genre_ids"] = None
    added.at[1, "keyword_ids"] = None
    added.at[1, "overview"] = None
    added.at[2, "genre_ids"] = [999999991]
    added.at[2, "keyword_ids"] = [999999993]
    added.at[2, "overview"] = "zzzz_v2_oov_9837621_only"
    return added


def frozen_quality(frame, bundle, x, active, np, pd):
    r = frame.raw_vote_average_number.to_numpy(float)
    v = frame.raw_vote_count_number.to_numpy(float)
    valid = frame.quality_state.eq("VALID").to_numpy()
    assert np.isfinite(r[valid]).all() and np.isfinite(v[valid]).all()
    assert ((r[valid] > 0) & (r[valid] <= 10) & (v[valid] > 0) & (v[valid] == np.floor(v[valid]))).all()
    q = np.full(len(frame), np.nan)
    C, m = bundle["quality"]["C"], bundle["quality"]["m"]
    q[valid] = (v[valid] * r[valid] + m * C) / (v[valid] + m)
    date = pd.to_datetime(frame.release_date, format="%Y-%m-%d", errors="coerce")
    eligible = (date.notna() & (date <= pd.Timestamp(bundle["candidate_date"])) & frame.status.eq("Released") & frame.raw_adult_state.eq("FALSE") & frame.raw_video_state.eq("FALSE")).to_numpy()
    legal = np.flatnonzero(eligible & valid & active & (np.sum(x * x, axis=1) > 1e-12))
    ids = frame.service_movie_id.to_numpy()
    return q, legal[np.lexsort((ids[legal], -v[legal], -q[legal]))]


def overlap(a, b):
    a, b = list(a), list(b)
    sa, sb = set(a), set(b)
    common = len(sa & sb)
    return {"before": len(a), "after": len(b), "intersection": common,
            "retained_fraction": common / len(a) if a else None,
            "after_fraction": common / len(b) if b else None,
            "jaccard": common / len(sa | sb) if sa | sb else None,
            "ordered_equal": a == b, "removed": sorted(sa - sb), "added": sorted(sb - sa)}


def distribution(values, np):
    a = np.asarray([v for v in values if v is not None], float)
    if not len(a):
        return {"n": 0}
    return {"n": len(a), "min": float(a.min()), "median": float(np.median(a)), "mean": float(a.mean()),
            "max": float(a.max()), "max_abs": float(np.abs(a).max()), "zero": int((a == 0).sum()),
            "positive": int((a > 0).sum()), "negative": int((a < 0).sum())}


def run(args):
    reviewed = read(args.review)
    fp = fingerprint()
    assert reviewed["status"] == "PASS" and reviewed["fingerprint"] == fp, "exact independent pre-execution review required"
    review_pin = pin(args.review)
    assert pin(OUT / "final-seal.json")["sha256"] == args.final_seal_sha256
    assert not DEST.exists(), "preserve any previous attempt; fresh output directory required"
    final_seal = verify_inventory("final")
    prepare_seal = verify_inventory("prepare")
    manifest = read(OUT / "final/manifest.json")
    assert "bundle" in manifest and "policy" in manifest, "no frozen exemplar available"
    assert manifest["bundle"] == pin(OUT / "final/bundle.pkl")
    assert manifest["source_seals"]["prepare"] == prepare_seal
    source_seals = {stage: pin(OUT / (stage + "-seal.json")) for stage in manifest["source_seals"]}
    assert source_seals == manifest["source_seals"]
    assert manifest["code"] == {name: pin(ROOT / "scripts" / name) for name in CODE}

    import numpy as np
    import pandas as pd
    import psutil
    from threadpoolctl import threadpool_limits
    sys.path.insert(0, str(ROOT / "scripts"))
    import dv2_common as common
    from dv2_retrieve import Engine
    import dv2_retrieve
    assert Path(common.__file__).resolve() == (ROOT / "scripts/dv2_common.py").resolve()
    assert Path(dv2_retrieve.__file__).resolve() == (ROOT / "scripts/dv2_retrieve.py").resolve()
    assert "dv2_predictor" not in sys.modules and "dv2_runtime" not in sys.modules
    assert common.OUT.resolve() == OUT.resolve() and common.CFG["threads"] == 2
    assert psutil.virtual_memory().available >= LIMITS["minimum_free_gib"] * 2**30
    start = time.perf_counter()
    stopped = threading.Event()
    peak = [0]
    DEST.mkdir(parents=True, exist_ok=False)

    def monitor():
        while not stopped.wait(1):
            peak[0] = max(peak[0], psutil.Process().memory_info().rss)
            bad = time.perf_counter() - start > LIMITS["seconds"] or peak[0] > LIMITS["rss_gib"] * 2**30
            bad |= psutil.virtual_memory().available < LIMITS["minimum_free_gib"] * 2**30
            bad |= sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file()) > LIMITS["output_mib"] * 2**20
            if bad:
                write(DEST / "resource-stop.json", {"status": "RESOURCE_STOP", "seconds": time.perf_counter() - start, "peak_rss": peak[0]})
                os._exit(86)

    threading.Thread(target=monitor, daemon=True).start()
    with threadpool_limits(limits=2):
        with (OUT / "final/bundle.pkl").open("rb") as stream:
            bundle = pickle.load(stream)
        rules = ["underseen_count", "underseen_share", "profile_prior", "profile_prior_mean", "profile_epsilon", "multi_temperature", "return_k"]
        assert bundle["runtime_rules"] == {k: common.CFG[k] for k in rules}
        import platform, scipy, sklearn
        versions = {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__, "sklearn": sklearn.__version__}
        assert bundle["runtime_versions"] == versions == manifest["runtime_versions"]
        material = material_digest(bundle, common.array_hash)
        assert material == manifest["rules_digest"] and bundle["policy"] == manifest["policy"]
        policy = dict(bundle["policy"])
        assert policy.get("kind", "group") == "group", "this bounded frozen-representative diagnostic requires the frozen group exemplar"
        frame = pd.read_parquet(OUT / "prepare/catalog.parquet")
        assert len(frame) == 237817 and frame.service_movie_id.is_unique
        ids = frame.service_movie_id.to_numpy()
        x = np.load(OUT / "prepare/content.npy", mmap_mode="r")
        genre = np.load(OUT / "prepare/genre.npy", mmap_mode="r")
        assert common.array_hash(x) == bundle["source_space_hashes"]["GKT"]
        assert common.array_hash(genre) == bundle["source_space_hashes"]["G"]
        assigned = pd.read_parquet(OUT / "final/assignments.parquet", columns=["service_movie_id", "taste_id", "child_id", "group_id"])
        np.testing.assert_array_equal(assigned.service_movie_id, ids)
        np.testing.assert_array_equal(assigned.taste_id, frame.taste_id)
        groups = assigned.group_id.to_numpy(int)
        offsets = np.cumsum([0] + [len(c) for c in bundle["child_centers"]])
        np.testing.assert_array_equal(groups, offsets[assigned.taste_id] + assigned.child_id)
        q0, order0 = frozen_quality(frame, bundle, x, np.ones(len(frame), bool), np, pd)
        np.testing.assert_array_equal(q0, np.load(OUT / "prepare/quality.npy"))
        np.testing.assert_array_equal(order0, np.load(OUT / "prepare/quality-order.npy"))
        contexts, redactions = redact_contexts(OUT / "prepare/contexts.json")
        roles = read(OUT / "prepare/roles.json")
        assert len(roles["verification"]) == 180 and len(roles["validation"]) == 90
        assert not set(roles["verification"]) & set(roles["validation"])
        cx = genre if policy.get("space", "GKT") == "G" else x
        eligible = sorted([c for c in contexts if c["cap"] == 10 and c["uid"] in roles["verification"] and common.profile(cx, c, policy.get("profile_mode", "signed"))[1]["state"] == "VALID"], key=lambda c: c["uid"])
        assert len(eligible) >= LIMITS["users"]
        chosen = eligible[:LIMITS["users"]]
        invariant = read(OUT / "final/invariance.json")
        removed = [int(i) for i in bundle["representatives"]["medoid_ids"] if int(i) > 0][:8]
        serving = invariant["actual_serving_request_parity"]
        history_fixture_uid = int(serving[0]["uid"]) if serving else None
        if serving:
            source_c = next(c for c in contexts if c["uid"] == history_fixture_uid and c["cap"] == 10)
            removed.append(int(ids[source_c["history"][0]]))
        removed = sorted(set(removed))
        assert removed == invariant["removed_medoid_or_history_ids"] and set(removed) <= set(ids)
        added = append_fixture(frame, np)
        blocks, _ = common.transform(added, bundle["preprocessor"])
        top = common.nearest(common.combine(blocks, bundle["top_weights"]), bundle["top_centers"])[0]
        sx = common.combine(blocks, bundle["sub_weights"])
        child = np.zeros(3, int)
        for parent, centers in enumerate(bundle["child_centers"]):
            ix = np.flatnonzero(top == parent)
            child[ix] = common.nearest(sx[ix], centers)[0]
        new_x = common.combine(blocks, bundle["content_weights"])
        new_groups = offsets[top] + child
        assert not (np.sum(new_x[-2:] ** 2, axis=1) > 1e-12).any()
        assert new_groups[0] == groups[0]
        np.testing.assert_allclose(new_x[0], x[0], rtol=1e-6, atol=1e-7)
        combined = pd.concat([frame, added], ignore_index=True)
        ids_all = combined.service_movie_id.to_numpy()
        assert combined.service_movie_id.is_unique and not set(added.service_movie_id) & set(ids)
        xx = np.concatenate([x, new_x])
        gg = np.concatenate([genre, blocks[0]])
        all_groups = np.concatenate([groups, new_groups])
        states = []
        for name, active in [("before", np.r_[np.ones(len(frame), bool), np.zeros(3, bool)]), ("append", np.ones(len(combined), bool)), ("append_delete", ~combined.service_movie_id.isin(removed).to_numpy())]:
            q, order = frozen_quality(combined, bundle, xx, active, np, pd)
            np.testing.assert_array_equal(q[:len(frame)], q0)
            if name == "before":
                np.testing.assert_array_equal(order, order0)
            if name == "append_delete":
                assert not set(ids_all[order]) & set(removed)
            hierarchy = {"groups": all_groups, "n_groups": int(offsets[-1]), "representatives": bundle["representatives"], "space_hashes": bundle["source_space_hashes"]}
            states.append((name, Engine(hierarchy, xx, gg, combined, order, q), active))
        phases = {name: {"active_catalog": int(active.sum()), "quality_eligible": len(engine.order), "q_order_sha256": common.array_hash(ids_all[engine.order])} for name, engine, active in states}
        comparisons, users = [], []
        group_metrics = {transition: {"full_mean": [], "prefix_mean": [], "full_count": [], "prefix_count": [], "changed_pool": 0, "changed_prefix": 0, "rows": 0} for transition in ["before_to_append", "append_to_append_delete", "before_to_append_delete"]}
        with gzip.open(DEST / "pools.jsonl.gz", "wt", encoding="utf-8") as pool_stream, gzip.open(DEST / "group-deltas.jsonl.gz", "wt", encoding="utf-8") as delta_stream:
            for c in chosen:
                snapshots = {}
                base_p, _ = common.profile(gg if policy.get("space", "GKT") == "G" else xx, c, policy.get("profile_mode", "signed"))
                similarities = (gg if policy.get("space", "GKT") == "G" else xx) @ base_p
                for name, engine, active in states:
                    candidates, info, state = engine.retrieve(c, policy)
                    assert state["profile"]["state"] == "VALID" and info["global_fill"] == 0
                    np.testing.assert_array_equal(state["p"], base_p)
                    expected_counts = np.bincount(all_groups[c["viewed"]], minlength=engine.n)
                    expected_under = (expected_counts <= bundle["runtime_rules"]["underseen_count"]) & (expected_counts / max(1, len(c["viewed"])) <= bundle["runtime_rules"]["underseen_share"])
                    np.testing.assert_array_equal(state["counts"], expected_counts)
                    np.testing.assert_array_equal(state["under"], expected_under)
                    unseen = engine.order[~np.isin(engine.order, c["viewed"])]
                    available = unseen[expected_under[all_groups[unseen]]]
                    pools = {}
                    # Independently stable-partition the global quality order once.
                    partition = available[np.argsort(all_groups[available], kind="stable")]
                    bounds = np.searchsorted(all_groups[partition], np.arange(engine.n + 1))
                    for g in np.flatnonzero(np.diff(bounds)):
                        full = partition[bounds[g]:bounds[g + 1]]
                        prefix = full[:policy["quota"]]
                        np.testing.assert_array_equal(prefix, state["pools"][int(g)])
                        row = {"uid": int(c["uid"]), "scenario": name, "group_id": int(g), "full_count": len(full), "prefix_count": len(prefix), "full_ids": ids_all[full].tolist(), "prefix_ids": ids_all[prefix].tolist(), "full_mean_similarity": float(np.mean(similarities[full])), "prefix_mean_similarity": float(np.mean(similarities[prefix]))}
                        pool_stream.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + "\n")
                        pools[int(g)] = row
                    assert sorted(pools) == state["legal"].tolist()
                    assert active[candidates].all() and not set(candidates) & set(c["viewed"])
                    assert len(candidates) <= policy["budget"] and len(set(candidates)) == len(candidates)
                    snapshot = {"candidate_ids": ids_all[candidates].tolist(), "legal_groups": sorted(pools), "underseen_groups": np.flatnonzero(expected_under).tolist(), "full_pool_count": sum(v["full_count"] for v in pools.values()), "prefix_pool_count": sum(v["prefix_count"] for v in pools.values()), "group_order": info.get("group_order", []), "visited_groups": info["visited_groups"], "candidate_groups": info["candidate_groups"], "state": info["state"], "candidate_mean_similarity": float(np.mean(similarities[candidates])) if len(candidates) else None, "pools": pools}
                    snapshots[name] = snapshot
                for left, right in [("before", "append"), ("append", "append_delete"), ("before", "append_delete")]:
                    label = left + "_to_" + right
                    a, b = snapshots[left], snapshots[right]
                    metric = group_metrics[label]
                    for g in sorted(set(a["pools"]) | set(b["pools"])):
                        ar, br = a["pools"].get(g), b["pools"].get(g)
                        full = overlap(ar["full_ids"] if ar else [], br["full_ids"] if br else [])
                        prefix = overlap(ar["prefix_ids"] if ar else [], br["prefix_ids"] if br else [])
                        full_delta = br["full_mean_similarity"] - ar["full_mean_similarity"] if ar and br else None
                        prefix_delta = br["prefix_mean_similarity"] - ar["prefix_mean_similarity"] if ar and br else None
                        change = {"uid": int(c["uid"]), "transition": label, "group_id": g, "full_pool": full, "q_prefix": prefix, "full_mean_similarity_delta": full_delta, "prefix_mean_similarity_delta": prefix_delta}
                        delta_stream.write(json.dumps(change, ensure_ascii=True, allow_nan=False) + "\n")
                        metric["rows"] += 1
                        metric["changed_pool"] += int(not full["ordered_equal"])
                        metric["changed_prefix"] += int(not prefix["ordered_equal"])
                        metric["full_mean"].append(full_delta)
                        metric["prefix_mean"].append(prefix_delta)
                        metric["full_count"].append(full["after"] - full["before"])
                        metric["prefix_count"].append(prefix["after"] - prefix["before"])
                    comparisons.append({"uid": int(c["uid"]), "transition": label, "candidates": overlap(a["candidate_ids"], b["candidate_ids"]), "vanished_groups": sorted(set(a["legal_groups"]) - set(b["legal_groups"])), "new_groups": sorted(set(b["legal_groups"]) - set(a["legal_groups"])), "group_order_equal": a["group_order"] == b["group_order"], "candidate_mean_similarity_delta": b["candidate_mean_similarity"] - a["candidate_mean_similarity"] if a["candidate_mean_similarity"] is not None and b["candidate_mean_similarity"] is not None else None})
                users.append({"uid": int(c["uid"]), "cap": 10, "profile_sha256": common.array_hash(base_p), "removed_history_ids": sorted(set(ids[np.asarray(c["history"], int)]) & set(removed)), "removed_viewed_ids": sorted(set(ids[np.asarray(c["viewed"], int)]) & set(removed)), "scenarios": {k: {rk: rv for rk, rv in v.items() if rk != "pools"} for k, v in snapshots.items()}})
                print("USER_DONE", c["uid"], len(users), flush=True)
        assert material_digest(bundle, common.array_hash) == material
        assert common.array_hash(xx[:len(frame)]) == bundle["source_space_hashes"]["GKT"]
        assert common.array_hash(gg[:len(frame)]) == bundle["source_space_hashes"]["G"]
        assert "dv2_predictor" not in sys.modules and "dv2_runtime" not in sys.modules
        assert fingerprint() == fp and pin(args.review) == review_pin
        assert verify_inventory("final") == final_seal and verify_inventory("prepare") == prepare_seal
        assert {stage: pin(OUT / (stage + "-seal.json")) for stage in source_seals} == source_seals
        summaries = {}
        for label, metrics in group_metrics.items():
            subset = [r for r in comparisons if r["transition"] == label]
            summaries[label] = {**{k: metrics[k] for k in ["rows", "changed_pool", "changed_prefix"]}, "full_count_delta": distribution(metrics["full_count"], np), "prefix_count_delta": distribution(metrics["prefix_count"], np), "full_mean_similarity_delta": distribution(metrics["full_mean"], np), "prefix_mean_similarity_delta": distribution(metrics["prefix_mean"], np), "candidate_retained_fraction": distribution([r["candidates"]["retained_fraction"] for r in subset], np), "candidate_mean_similarity_delta": distribution([r["candidate_mean_similarity_delta"] for r in subset], np), "users_with_candidate_change": sum(not r["candidates"]["ordered_equal"] for r in subset), "vanished_group_occurrences": sum(len(r["vanished_groups"]) for r in subset), "new_group_occurrences": sum(len(r["new_groups"]) for r in subset)}
        # Cast NumPy service-ID scalars from set intersections to plain JSON ints.
        for user in users:
            for field in ["removed_history_ids", "removed_viewed_ids"]:
                user[field] = list(map(int, user[field]))
        write(DEST / "users.json", users)
        write(DEST / "comparisons.json", comparisons)
        report = {"status": "PASS", "claim_scope": "Actual frozen group representative and Q-prefix supply drift under exactly the freeze append/tombstone fixtures; no prediction, refit, target-label evaluation, or policy selection.", "root": str(ROOT), "fingerprint": fp, "review": {"path": str(Path(args.review).resolve()), **review_pin}, "final_seal": final_seal, "prepare_seal": prepare_seal, "source_seals": source_seals, "bundle": manifest["bundle"], "material_digest_before": material, "material_digest_after": material_digest(bundle, common.array_hash), "frozen_material_unchanged": True, "policy": policy, "selection": {"rule": "uid ascending first20 check/verification cap10 users with VALID frozen policy space/profile mode", "eligible_valid_check_users": len(eligible), "uids": [int(c["uid"]) for c in chosen]}, "contexts": {"future_numeric_arrays_redacted_before_json_parse": redactions, "future_rating_values_parsed": 0, "past_history_preserved_after_tombstone": True}, "fixture": {"added_service_ids": added.service_movie_id.tolist(), "added_group_ids": new_groups.tolist(), "added_trained_content_support": (np.sum(new_x * new_x, axis=1) > 1e-12).tolist(), "removed_service_ids": removed, "freeze_history_fixture_uid": history_fixture_uid, "delete_semantics": "active mask only; metadata, groups, history and viewed records retained"}, "scenarios": phases, "summary": summaries, "model_predictions": 0, "runtime_versions": versions, "limits": LIMITS, "wall_seconds": time.perf_counter() - start, "peak_rss": max(peak[0], psutil.Process().memory_info().rss)}
        write(DEST / "report.json", report)
        assert sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file()) <= LIMITS["output_mib"] * 2**20
        write(DEST / "seal.json", {"stage": "mutation-prefix-drift-r2", "files": {p.name: pin(p) for p in sorted(DEST.iterdir()) if p.is_file()}})
        stopped.set()
        print(json.dumps({"status": "PASS", "report": pin(DEST / "report.json"), "seal": pin(DEST / "seal.json"), "output": str(DEST)}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--review", type=Path)
    parser.add_argument("--final-seal-sha256")
    arguments = parser.parse_args()
    if arguments.run:
        assert arguments.review and arguments.final_seal_sha256, "review ledger and explicit finalized seal hash required"
        run(arguments)
    else:
        print(json.dumps(fingerprint(), indent=2, ensure_ascii=True))
