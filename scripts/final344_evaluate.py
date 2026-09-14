"""Frozen FM/GBT development calibration, budget selection and final evaluation.

Importing this module performs no experiment I/O. The four CLI phases open
previously used development labels only after the review/input/model gates.
"""
import argparse
import importlib
import re
import time

import numpy as np
import pandas as pd

CAPS = (0, 1, 5, 10, 30)
SEEDS = (339, 344, 345)
FAMILIES = ("FM", "GBT")
INITIAL = ("FM150_s339", "FM300_s339", "GBT60_s339", "GBT120_s339")
LABEL_SHA = "e3bf301a6e2ea7885b59bcab7fe83c2d3ad84f93f2f1bbb2d54d943b9a658db8"
ID_PATTERN = re.compile(r"^(FM150|FM300|GBT60|GBT120)_s(339|344|345)$")
QUALITY_KEYS = ("ndcg", "stars", "low", "good", "both_low")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def api():
    return importlib.import_module("final344_common")


def config():
    a = api()
    return a.read(a.ROOT / "docs/recommendation/plans/final-fm-gbt/config.json")


def parse_fit_id(fit_id):
    match = ID_PATTERN.fullmatch(fit_id)
    require(match is not None, "declared fit ID")
    recipe, seed = match.groups()
    return recipe, int(seed), "FM" if recipe.startswith("FM") else "GBT"


def valid_stars(values):
    y = np.asarray(values, dtype=np.float64)
    return bool(np.isfinite(y).all() and np.isin(y, np.arange(1, 11) / 2).all())


def affine_by_user(records, min_users=20, min_rows=40):
    """DataFrame uid/movie_id/raw/rating; each contributing user has weight 1."""
    require(not records.duplicated(["uid", "movie_id"]).any(), "unique calibration rows")
    require(valid_stars(records.rating), "actual calibration half stars")
    require(np.isfinite(records.raw.to_numpy(float)).all(), "finite calibration predictions")
    users = int(records.uid.nunique())
    result = {"users": users, "rows": len(records), "a": None, "b": None,
              "variance": None, "state": "INSUFFICIENT"}
    if users < min_users or len(records) < min_rows:
        return result
    weights = 1 / records.groupby("uid").uid.transform("size").to_numpy(float)
    weights /= weights.sum()
    x = records.raw.to_numpy(float)
    y = records.rating.to_numpy(float)
    mx, my = float(weights @ x), float(weights @ y)
    variance = float(weights @ ((x - mx) ** 2))
    covariance = float(weights @ ((x - mx) * (y - my)))
    require(np.isfinite([mx, my, variance, covariance]).all(), "finite calibration moments")
    slope = 0. if variance <= 1e-12 else max(0., covariance / variance)
    return {**result, "a": my - slope * mx, "b": slope, "variance": variance,
            "covariance": covariance, "state": "CONSTANT" if slope == 0 else "AFFINE"}


def quality(y, raw, ids, end, start=0):
    """Complete prefix/page only. Ranking always uses raw scores and exact ID ties."""
    y, raw, ids = np.asarray(y, float), np.asarray(raw, float), np.asarray(ids)
    require(len(y) == len(raw) == len(ids), "same quality axes")
    require(len(np.unique(ids)) == len(ids), "unique quality movies")
    require(np.isfinite(raw).all() and valid_stars(y), "finite raw and real ratings")
    require(0 <= start < end, "legal page boundaries")
    result = {k: np.nan for k in QUALITY_KEYS}
    result["returned"] = min(max(len(y) - start, 0), end - start)
    if len(y) < end:
        return result
    order = np.lexsort((ids, -raw))
    shown = y[order[start:end]]
    result.update(stars=float(shown.mean()), low=float((shown <= 2).mean()),
                  good=float((shown >= 4).mean()))
    if end - start == 2:
        result["both_low"] = float((shown <= 2).all())
    if start == 0:
        gains = (y - .5) / 4.5
        discounts = 1 / np.log2(np.arange(end) + 2)
        ideal = float(np.sort(gains)[::-1][:end] @ discounts)
        if ideal > 0:
            result["ndcg"] = float(gains[order[:end]] @ discounts / ideal)
    return result


def error_metrics(y, raw, calibration):
    y, raw = np.asarray(y, float), np.asarray(raw, float)
    require(len(y) == len(raw) and valid_stars(y) and np.isfinite(raw).all(), "error axes")
    calibrated = np.full(len(y), np.nan)
    if calibration is not None and calibration.get("a") is not None:
        calibrated = calibration["a"] + calibration["b"] * raw
    result = {}
    for kind, values in (("native", np.clip(raw, .5, 5)), ("raw", raw),
                         ("calibrated", np.clip(calibrated, .5, 5)),
                         ("calibrated_raw", calibrated)):
        error = values - y
        for metric, vector in (("mse", error ** 2), ("mae", abs(error)), ("bias", error)):
            result[kind + "_" + metric] = float(vector.mean()) if len(y) else np.nan
        result[kind + "_rmse"] = float(np.sqrt(result[kind + "_mse"]))
    result["outside_scale"] = float(((raw < .5) | (raw > 5)).mean()) if len(y) else np.nan
    result["calibrated_outside_scale"] = (float(((calibrated < .5) | (calibrated > 5)).mean())
        if len(y) and np.isfinite(calibrated).all() else np.nan)
    return result


def select_budget(smaller, larger):
    def usable(candidate):
        return candidate is not None and candidate.get("status") == "SUCCESS" and all(
            candidate.get(k) is not None and np.isfinite(candidate[k]) for k in ("mse", "ndcg2"))
    if not usable(smaller):
        return {"choice": None, "reason": "BASELINE_UNAVAILABLE"}
    if not usable(larger):
        return {"choice": smaller["recipe"], "reason": "LARGER_UNAVAILABLE"}
    dominates = larger["mse"] <= smaller["mse"] and larger["ndcg2"] >= smaller["ndcg2"]
    strict = larger["mse"] < smaller["mse"] or larger["ndcg2"] > smaller["ndcg2"]
    return {"choice": larger["recipe"] if dominates and strict else smaller["recipe"],
            "reason": "LARGER_PARETO_IMPROVEMENT" if dominates and strict else "SMALLER_TIE_OR_TRADEOFF"}


def bootstrap_difference(before, after, samples=20000, seed=344, confidence=.9875, minimum=30):
    x, y = np.asarray(before, float), np.asarray(after, float)
    require(x.shape == y.shape and x.ndim == 1 and np.isfinite(x).all() and np.isfinite(y).all(), "paired finite users")
    delta = y - x
    result = {"users": len(x), "before_mean": float(x.mean()) if len(x) else None,
              "after_mean": float(y.mean()) if len(y) else None,
              "delta": float(delta.mean()) if len(delta) else None,
              "ci_low": None, "ci_high": None, "confidence": confidence}
    if len(delta) < minimum:
        return result
    rng = np.random.default_rng(seed)
    boot = np.empty(samples)
    # Chunking preserves the generator's exact flattened index stream.
    for start in range(0, samples, 256):
        end = min(samples, start + 256)
        boot[start:end] = delta[rng.integers(len(delta), size=(end-start, len(delta)))].mean(axis=1)
    alpha = (1 - confidence) / 2
    result["ci_low"], result["ci_high"] = map(float, np.quantile(boot, [alpha, 1-alpha]))
    return result


def four_way_decision(contrasts):
    require(len(contrasts) == 4 and {(r["group"], r["metric"]) for r in contrasts} ==
            {(g, m) for g in ("ALL", "C") for m in ("calibrated_mse", "ndcg2")}, "four fixed contrasts")
    if any(r["ci_low"] is None or r["ci_high"] is None for r in contrasts):
        return "NO_WINNER_INCOMPLETE_OR_INSUFFICIENT"
    def wins(family):
        return all((r["ci_high"] < 0 if ((r["metric"] == "calibrated_mse") == (family == "GBT"))
                    else r["ci_low"] > 0) for r in contrasts)
    if wins("GBT"):
        return "GBT_CONSISTENT_OBSERVED_IMPROVEMENT"
    if wins("FM"):
        return "FM_CONSISTENT_OBSERVED_IMPROVEMENT"
    return "NO_CLEAR_WINNER_OR_TRADEOFF"


def label_guard():
    a = api()
    result = a.pin(a.OLD / "labels.parquet")
    require(result["sha256"] == LABEL_SHA, "previously opened development labels only")
    return result


def gate(fit_ids):
    a = api()
    a.reviewed("evaluation")
    a.lock()
    execution = a.read(a.DOC / "execution.json")
    require(execution.get("evaluation_scope") == "DEVELOPMENT_ONLY", "new confirmation path not authorized here")
    require(a.pin(a.OUT / "roles.csv") == a.pin(a.LEGACY / "roles.csv"), "exact reused calibration/comparison roles")
    audit_path = a.OUT / "final-model-audit-seal.json"
    if not audit_path.exists():
        audit_path = a.OUT / "model-audit-seal.json"
    audit = a.read(audit_path)
    require(audit.get("status") == "PASS", "independent model audit PASS before labels")
    outcomes, parents = {}, {}
    for fit_id in fit_ids:
        recipe, seed, _ = parse_fit_id(fit_id)
        a.verify(fit_id + "-seal.json")
        parent = a.pin(a.OUT / (fit_id + "-seal.json"))
        outcome = a.read(a.OUT / fit_id / "outcome.json")
        require(outcome.get("fit_id") == fit_id and outcome.get("recipe") == recipe and outcome.get("seed") == seed, "fit outcome identity")
        require(outcome.get("status") in {"SUCCESS", "FAILED", "TIMEOUT", "AUDIT_FAILED"}, "declared outcome")
        require(outcome.get("resource_status") in {"PASS", "EXCEPTION", "UNKNOWN"}, "resource outcome separate from quality")
        if outcome["status"] == "SUCCESS":
            require(audit.get("fit_seals", {}).get(fit_id) == parent, "independently audited exact successful fit")
            checks = a.read(a.OUT / fit_id / "checks.json")
            require(checks.get("partition_equal") is True and checks.get("params_equal") is True, "same partition and resolved params")
        parents[fit_id] = parent
        outcomes[fit_id] = outcome
    state = {"execution": a.fingerprint("evaluation"), "execution_contract": a.pin(a.DOC / "execution.json"),
             "fit_seals": parents, "model_audit": a.pin(audit_path),
             "roles": a.pin(a.OUT / "roles.csv"), "labels": label_guard()}
    return state, outcomes


def validate_contexts(contexts, catalog, roles, origin, horizon_days):
    ids = catalog.movie_id.to_numpy()
    require(len(ids) == len(np.unique(ids)) and np.all(ids[:-1] < ids[1:]), "sorted unique movie axis")
    require(set(roles.values()) == {"calibration", "comparison"}, "known reused roles")
    require(sum(r == "calibration" for r in roles.values()) == 90 and sum(r == "comparison" for r in roles.values()) == 180, "90/180 development users")
    seen, total = {}, 0
    for c in contexts:
        uid, cap = int(c["uid"]), int(c["cap"])
        require(uid in roles and cap in CAPS and (uid, cap) not in seen, "unique user/cap")
        ei, oi = np.asarray(c["ei"], int), np.asarray(c["oi"], int)
        require(len(ei) > 0 and len(np.unique(ei)) == len(ei) and ((0 <= ei) & (ei < len(ids))).all(), "target indices")
        require(len(oi) == c["h"] <= cap and len(c["stars"]) == len(oi), "actual input size")
        require(valid_stars(c["stars"]) and not np.intersect1d(ei, oi).size, "input stars and target separation")
        require(len(np.unique(oi)) == len(oi) and ((0 <= oi) & (oi < len(ids))).all(), "input indices")
        require(len(c["input_timestamps"]) == len(oi) and all(t < origin for t in c["input_timestamps"]), "strict-past evaluation input")
        require(len(c["target_timestamps"]) == len(ei) and all(origin <= t < origin+horizon_days*86400 for t in c["target_timestamps"]), "target time window")
        require(c["start"] == total and c["stop"] == total+len(ei), "contiguous score rows")
        require(catalog.released_at_origin.to_numpy()[ei].all(), "observed candidates available at origin")
        require(not np.intersect1d(ei, c["viewed"]).size, "no already watched target")
        total = c["stop"]
        seen[(uid, cap)] = c
    require(set(seen) == {(u, k) for u in roles for k in CAPS}, "every role user has every cap")
    for uid in roles:
        base = seen[(uid, 0)]
        require(base["h"] == 0, "cap0 truly empty")
        for cap in CAPS[1:]:
            c = seen[(uid, cap)]
            require(c["ei"] == base["ei"] and c["target_timestamps"] == base["target_timestamps"], "same targets for real versus empty O")
    return total


def load_data(outcomes):
    a, cfg = api(), config()
    catalog = pd.read_parquet(a.OLD / "catalog.parquet")
    roles_frame = pd.read_csv(a.OUT / "roles.csv")
    require(not roles_frame.uid.duplicated().any(), "one role per user")
    roles = dict(zip(roles_frame.uid.astype(int), roles_frame.role))
    contexts = a.read(a.OLD / "contexts.json")
    count = validate_contexts(contexts, catalog, roles, cfg["origin_timestamp"], cfg["horizon_days"])
    # Mandatory immutable label pin immediately before the actual read.
    label_guard()
    label_frame = pd.read_parquet(a.OLD / "labels.parquet")
    require(not label_frame.duplicated(["uid", "movie_id"]).any() and valid_stars(label_frame.rating), "unique real label rows")
    labels = label_frame.set_index(["uid", "movie_id"]).rating
    predictions = {}
    for fit_id, outcome in outcomes.items():
        if outcome["status"] != "SUCCESS":
            continue
        p = np.load(a.OUT / fit_id / "predictions.npy", allow_pickle=False)
        require(p.shape == (count,) and np.isfinite(p).all(), "full fixed-axis finite model predictions")
        predictions[fit_id] = p
    return catalog, contexts, roles, labels, predictions


def truth(c, catalog, labels):
    ei = np.asarray(c["ei"], int)
    ids = catalog.movie_id.to_numpy()[ei]
    keys = pd.MultiIndex.from_arrays([np.full(len(ei), c["uid"]), ids])
    y = labels.reindex(keys).to_numpy(float)
    require(valid_stars(y), "every observed candidate has a real half-star label")
    return ei, ids, y


def selected_fit_ids():
    a = api()
    a.verify("selection-seal.json")
    selection = a.read(a.OUT / "selection.json")
    require(selection.get("status") == "PASS" and set(selection["selected"]) == set(FAMILIES)
            and all(selection["selected"][f] for f in FAMILIES), "both selected recipes")
    ids = [selection["selected"][f] + "_s" + str(s) for f in FAMILIES for s in SEEDS]
    for family in FAMILIES:
        require(parse_fit_id(selection["selected"][family]+"_s339")[2] == family, "selected family identity")
    return ids, selection


def preserve(paths):
    a = api()
    require(not any((a.OUT / name).exists() for name in paths), "preserve existing phase outputs")


def verify_parent_snapshot(name, expected):
    """Recheck both the original seal bytes and the artifacts sealed by it."""
    a = api()
    require(a.pin(a.OUT / name) == expected, "frozen parent drift: " + name)
    a.verify(name)
    require(a.pin(a.OUT / name) == expected, "frozen parent drift during verification: " + name)


def snapshot_parent(name):
    a = api()
    expected = dict(a.pin(a.OUT / name))
    verify_parent_snapshot(name, expected)
    return expected


def calibrate(final=False):
    a, cfg = api(), config()
    selection_parent = snapshot_parent("selection-seal.json") if final else None
    fit_ids = selected_fit_ids()[0] if final else list(INITIAL)
    prefix = "final" if final else "selection"
    files = [prefix + "-calibration.json"]
    preserve(files + [prefix + "-calibration-seal.json"])
    started, outcomes = gate(fit_ids)
    t = time.monotonic()
    catalog, contexts, roles, labels, predictions = load_data(outcomes)
    rows = []
    for fit_id, p in predictions.items():
        for cap in CAPS:
            chunks = []
            for c in contexts:
                if c["cap"] != cap or roles[c["uid"]] != "calibration":
                    continue
                _, ids, y = truth(c, catalog, labels)
                chunks.append(pd.DataFrame({"uid": c["uid"], "movie_id": ids,
                    "raw": p[c["start"]:c["stop"]], "rating": y}))
            frame = pd.concat(chunks, ignore_index=True)
            rows.append({"model": fit_id, "cap": cap, **affine_by_user(frame,
                cfg["calibration_min_users"], cfg["calibration_min_rows"])})
    payload = {"scope": "DEVELOPMENT_ONLY", "phase": prefix, "fits": rows,
               "outcomes": outcomes, "comparison_labels_used_for_fitting": False}
    a.write_json(a.OUT / files[0], payload)
    require(started == gate(fit_ids)[0], "calibration inputs/code drift")
    if final:
        verify_parent_snapshot("selection-seal.json", selection_parent)
    extra = {"selection_seal": selection_parent} if final else {}
    a.seal(prefix + "-calibration-seal.json", files, execution=started["execution"], evaluation_inputs=started,
           seconds=time.monotonic()-t, **extra)
    print(prefix.upper() + "_CALIBRATION_SEALED", flush=True)


def calibration_map(prefix):
    a = api()
    record = a.verify(prefix + "-calibration-seal.json")
    rows = a.read(a.OUT / (prefix + "-calibration.json"))["fits"]
    result = {(r["model"], int(r["cap"])): r for r in rows}
    require(len(result) == len(rows), "unique calibration model/cap")
    return result, record


def select():
    a = api()
    files = ["selection.json", "selection-user-metrics.parquet"]
    preserve(files + ["selection-seal.json"])
    started, outcomes = gate(INITIAL)
    calibration_parent = snapshot_parent("selection-calibration-seal.json")
    calibration, parent = calibration_map("selection")
    require(parent["evaluation_inputs"] == started, "selection calibration uses same reviewed fits/roles")
    catalog, contexts, roles, labels, predictions = load_data(outcomes)
    rows = []
    for c in contexts:
        if c["cap"] != 10 or c["h"] <= 0 or roles[c["uid"]] != "comparison":
            continue
        ei, ids, y = truth(c, catalog, labels)
        for model, prediction in predictions.items():
            p = prediction[c["start"]:c["stop"]]
            for group, mask in (("ALL", np.ones(len(y), bool)), ("C", catalog.blocked.to_numpy()[ei])):
                stats = error_metrics(y[mask], p[mask], calibration[(model, 10)])
                rows.append({"uid": c["uid"], "model": model, "group": group,
                    "targets": int(mask.sum()), "mse": stats["calibrated_mse"],
                    "ndcg2": quality(y[mask], p[mask], ids[mask], 2)["ndcg"]})
    frame = pd.DataFrame(rows, columns=["uid", "model", "group", "targets", "mse", "ndcg2"])
    candidates, families = {}, {}
    for model, outcome in outcomes.items():
        recipe, _, family = parse_fit_id(model)
        candidate = {"model": model, "recipe": recipe, "family": family,
            "status": outcome["status"], "resource_status": outcome["resource_status"],
            "mse": None, "ndcg2": None, "mse_users": 0, "ndcg2_users": 0}
        for group in ("ALL", "C"):
            sub = frame[frame.model.eq(model) & frame.group.eq(group)]
            group_result = {}
            for metric in ("mse", "ndcg2"):
                z = sub[metric].dropna()
                group_result[metric] = float(z.mean()) if len(z) else None
                group_result[metric + "_users"] = len(z)
            candidate[group] = group_result
            if group == "ALL":
                candidate.update(group_result)
        candidates[model] = candidate
    for family, small, large in (("FM", "FM150_s339", "FM300_s339"), ("GBT", "GBT60_s339", "GBT120_s339")):
        families[family] = select_budget(candidates[small], candidates[large])
    complete = all(families[f]["choice"] is not None for f in FAMILIES)
    selection = {"status": "PASS" if complete else "INCOMPLETE", "scope": "DEVELOPMENT_ONLY",
        "selected": {f: families[f]["choice"] for f in FAMILIES},
        "seed": 339, "cap": 10, "selection_group": "ALL", "h_group": "H_POSITIVE",
        "families": families, "candidates": candidates, "criteria": config()["selection_rule"],
        "C_used_for_budget_selection": False, "service_adoption": False}
    frame.to_parquet(a.OUT / files[1], index=False)
    a.write_json(a.OUT / files[0], selection)
    selection_output = dict(a.pin(a.OUT / "selection.json"))
    require(started == gate(INITIAL)[0], "selection inputs/code drift")
    verify_parent_snapshot("selection-calibration-seal.json", calibration_parent)
    require(a.pin(a.OUT / "selection.json") == selection_output, "new selection output drift")
    a.seal("selection-seal.json", files, execution=started["execution"], evaluation_inputs=started,
           calibration_seal=calibration_parent)
    print("BUDGET_SELECTION", selection["status"], selection["selected"], flush=True)


def old_file(folder, filename, seal_name="fit-seal.json"):
    a = api()
    record = a.read(folder / seal_name)
    require(a.pin(folder / filename) == record["files"][filename], "sealed descriptive reference file")
    return folder / filename


def references(row_count):
    """Old recipes and ALS are descriptive only, using their original calibrators."""
    a = api()
    f = np.load(old_file(a.FOUND, "predictions.npz"), allow_pickle=False)
    c = np.load(old_file(a.COMBO, "predictions.npz"), allow_pickle=False)
    values = {
        "REFERENCE_FM_RH": f["predictions"][:, f["names"].tolist().index("RH")],
        "REFERENCE_GBT_B": c["predictions"][:, c["names"].tolist().index("GBT")],
        "REFERENCE_ALS": c["predictions"][:, c["names"].tolist().index("ACTUAL_ALS")]}
    direct = c["actual_direct"].astype(bool)
    require(direct.shape == (row_count,), "reference direct axis")
    for name, p in values.items():
        require(p.shape == (row_count,), "old score row axis")
        require(np.array_equal(np.isfinite(p), direct) if name == "REFERENCE_ALS" else np.isfinite(p).all(), "old explicit support")
    oldcal = a.read(old_file(a.LEGACY, "calibration.json", "calibration-seal.json"))["fits"]
    mapping = {"FM_RH": "REFERENCE_FM_RH", "GBT_B": "REFERENCE_GBT_B", "ALS": "REFERENCE_ALS"}
    calibration = {(mapping[r["model"]], int(r["cap"])): r for r in oldcal if r["model"] in mapping}
    require(set(calibration) == {(n, cap) for n in values for cap in CAPS}, "old per-cap calibrators")
    parents = {str((folder / name).relative_to(a.ROOT)): a.pin(folder / name)
        for folder, name in ((a.FOUND, "fit-seal.json"), (a.COMBO, "fit-seal.json"),
                             (a.LEGACY, "calibration-seal.json"), (a.LEGACY, "calibration.json"))}
    return values, direct, calibration, parents


def metadata_masks(catalog):
    a = api()
    metadata = pd.read_parquet(a.BASE / "rec-ev-045/metadata.parquet")
    require(np.array_equal(metadata.movie_id, catalog.movie_id), "same metadata/catalog axis")
    release_frame = pd.read_parquet(a.OLD / "texts.parquet", columns=["movie_id", "release_date"])
    require(np.array_equal(release_frame.movie_id, catalog.movie_id), "release-date movie axis")
    dates = pd.to_datetime(release_frame.release_date, format="%Y-%m-%d", errors="coerce", utc=True)
    count, blocked = catalog.train_count.to_numpy(), catalog.blocked.to_numpy(bool)
    votes = metadata.tmdb_vote_count.to_numpy(float)
    year = dates.dt.year.to_numpy(float)
    masks = {"ALL": np.ones(len(catalog), bool), "C": blocked, "NATURAL_ZERO": ~blocked & (count == 0),
        "SUPPORT_0": count == 0, "SUPPORT_1_9": (count >= 1) & (count <= 9),
        "SUPPORT_10_49": (count >= 10) & (count <= 49), "SUPPORT_50_PLUS": count >= 50}
    for name, low, high in (("PRE1980", 0, 1979), ("1980_1999", 1980, 1999),
                           ("2000_2009", 2000, 2009), ("2010_2019", 2010, 2019),
                           ("2020_2022", 2020, 2022), ("2023_PLUS", 2023, np.inf)):
        masks["RELEASE_" + name] = (year >= low) & (year <= high)
    masks["RELEASE_MISSING"] = ~np.isfinite(year)
    masks["VOTES_ZERO_OR_MISSING"] = ~np.isfinite(votes) | (votes <= 0)
    for name, low, high in (("ONE", 1, 1), ("2_9", 2, 9), ("10_49", 10, 49),
                           ("50_499", 50, 499), ("500_PLUS", 500, np.inf)):
        masks["VOTES_" + name] = (votes >= low) & (votes <= high)
    novel = np.zeros(len(catalog), bool)
    for field in ("genre_ids", "keyword_ids", "director_ids", "top5_cast_ids", "production_country_codes"):
        lists = metadata[field].map(list).tolist()
        masks["MISSING_" + field] = np.array([len(v) == 0 for v in lists])
        training_values = {v for row, used in zip(lists, (count > 0) & ~blocked) if used for v in row}
        novel |= np.array([any(v not in training_values for v in row) for row in lists])
    languages = metadata.original_language.fillna("").astype(str).to_numpy()
    masks["MISSING_original_language"] = languages == ""
    trained_languages = set(languages[(count > 0) & ~blocked]) - {""}
    novel |= np.array([bool(v) and v not in trained_languages for v in languages])
    runtime = metadata.runtime_minutes.to_numpy(float)
    masks["MISSING_OR_NONPOSITIVE_runtime"] = ~np.isfinite(runtime) | (runtime <= 0)
    for field in ("tmdb_vote_average", "tmdb_vote_count", "tmdb_popularity"):
        vals = metadata[field].to_numpy(float)
        masks["MISSING_OR_NONPOSITIVE_" + field] = ~np.isfinite(vals) | (vals <= 0)
    masks["ANY_NEW_METADATA_CATEGORY"] = novel
    diagnostics = pd.DataFrame({"movie_id": catalog.movie_id, "support": count, "blocked": blocked,
        "release_year": year, "tmdb_vote_count": votes, "new_metadata_category": novel,
        **{k: v for k, v in masks.items() if k.startswith("MISSING_")}})
    return masks, diagnostics


def actual_h_band(h):
    return "0" if h == 0 else "1_4" if h < 5 else "5_9" if h < 10 else "10_29" if h < 30 else "30"


def activity_band(n):
    return "0" if n == 0 else "1_9" if n < 10 else "10_29" if n < 30 else "30_99" if n < 100 else "100_299" if n < 300 else "300_PLUS"


def model_info(name):
    if name in {"SEED_MEAN_FM", "SEED_MEAN_GBT"}:
        return {"kind": "SEED_MEAN_METRICS_NOT_ENSEMBLE", "recipe": name,
                "family": name.removeprefix("SEED_MEAN_"), "seed": -2}
    if name.startswith("REFERENCE_"):
        return {"kind": "DESCRIPTIVE_REFERENCE", "recipe": name, "family": "REFERENCE", "seed": -1}
    recipe, seed, family = parse_fit_id(name)
    return {"kind": "CANDIDATE", "recipe": recipe, "family": family, "seed": seed}


def build_metrics(catalog, contexts, roles, labels, predictions, calibration, direct, masks, movie_info):
    users, pages, errors, profiles = [], [], [], []
    info = {model: model_info(model) for model in predictions}
    for c in contexts:
        if roles[c["uid"]] != "comparison":
            continue
        ei, ids, y = truth(c, catalog, labels)
        sl = slice(c["start"], c["stop"])
        current_masks = {k: v[ei] for k, v in masks.items()}
        current_masks["W"] = ~catalog.blocked.to_numpy()[ei] & (catalog.train_count.to_numpy()[ei] > 0)
        current_masks["W_DIRECT"] = direct[sl]
        common = {"uid": c["uid"], "cap": c["cap"], "h": c["h"], "pre_all": c["pre_all"],
                  "actual_h": actual_h_band(c["h"]), "activity": activity_band(c["pre_all"])}
        oi = np.asarray(c["oi"], int)
        profiles.append({**common, "targets": len(ei), "C_targets": int(current_masks["C"].sum()),
            "natural_zero_targets": int(current_masks["NATURAL_ZERO"].sum()),
            "input_C": int(catalog.blocked.to_numpy()[oi].sum()),
            "input_natural_zero": int(((catalog.train_count.to_numpy()[oi] == 0) & ~catalog.blocked.to_numpy()[oi]).sum()),
            "als_supported_inputs": c["als_supported_inputs"]})
        for model, vector in predictions.items():
            raw = vector[sl]
            cr = calibration[(model, c["cap"])]
            for group, mask in current_masks.items():
                if model == "REFERENCE_ALS" and group != "W_DIRECT":
                    continue
                require(np.isfinite(raw[mask]).all(), "no model-dependent candidate removal")
                qy, qp, qi = y[mask], raw[mask], ids[mask]
                row = {**common, **info[model], "model": model, "group": group,
                       "targets": len(qy), "predicted": len(qp), **error_metrics(qy, qp, cr)}
                for end in (1, 2, 4, 6):
                    q = quality(qy, qp, qi, end)
                    row.update({k + str(end): q[k] for k in QUALITY_KEYS})
                users.append(row)
                for end in (2, 4, 6):
                    pages.append({**common, **info[model], "model": model, "group": group,
                        "end": end, "j": len(qy), **quality(qy, qp, qi, end, end-2)})
            # References retain unsupported rows as NaN; their direct-only label is explicit.
            native = np.clip(raw, .5, 5)
            cal_raw = np.full(len(raw), np.nan) if cr["a"] is None else cr["a"] + cr["b"]*raw
            cal = np.clip(cal_raw, .5, 5)
            part = movie_info.iloc[ei].reset_index(drop=True).copy()
            part = part.assign(**common, **info[model], model=model, rating=y, raw=raw,
                               calibrated_raw=cal_raw, native=native, calibrated=cal, direct=direct[sl])
            for kind, pred in (("native", native), ("calibrated", cal)):
                part[kind + "_se"] = (pred-y)**2
                part[kind + "_ae"] = abs(pred-y)
            errors.append(part)
    return pd.DataFrame(users), pd.DataFrame(pages), pd.concat(errors, ignore_index=True), pd.DataFrame(profiles)


def user_slices(frame):
    yield "population", "ALL", frame
    yield "population", "H_POSITIVE", frame[frame.h.gt(0)]
    yield "population", "H_ZERO", frame[frame.h.eq(0)]
    for key in ("actual_h", "activity"):
        for value, sub in frame.groupby(key, sort=True):
            yield key, str(value), sub


def summarize_users(frame, pages):
    identifiers = {"uid", "cap", "h", "pre_all", "actual_h", "activity", "kind", "recipe", "family", "seed", "model", "group", "targets", "predicted"}
    metrics = [c for c in frame if c not in identifiers]
    rows, page_rows = [], []
    for kind, value, sub in user_slices(frame):
        for cohort in ("natural", "common_j6"):
            used = sub if cohort == "natural" else sub[sub.targets.ge(6)]
            for (model, cap, group), data in used.groupby(["model", "cap", "group"], sort=True):
                base = {**model_info(model), "model": model, "cap": cap, "group": group,
                    "slice_type": kind, "slice": value, "cohort": cohort, "users": len(data)}
                for metric in metrics:
                    z = data[metric].dropna()
                    rows.append({**base, "metric": metric, "valid": len(z), "mean": float(z.mean()) if len(z) else np.nan})
                for metric in ("native_mae", "calibrated_mae"):
                    z = data[metric].dropna()
                    rows.append({**base, "metric": "p95_user_" + metric, "valid": len(z),
                                 "mean": float(z.quantile(.95)) if len(z) else np.nan})
    for kind, value, sub in user_slices(pages):
        for cohort in ("natural", "common_j6"):
            used = sub if cohort == "natural" else sub[sub.j.ge(6)]
            for (model, cap, group, end), data in used.groupby(["model", "cap", "group", "end"], sort=True):
                for metric in ("stars", "low", "good", "both_low"):
                    z = data[metric].dropna()
                    page_rows.append({**model_info(model), "model": model, "cap": cap, "group": group,
                        "end": end, "slice_type": kind, "slice": value, "cohort": cohort,
                        "metric": metric, "users": len(data), "valid": len(z),
                        "mean": float(z.mean()) if len(z) else np.nan})
    return pd.DataFrame(rows), pd.DataFrame(page_rows)


def summarize_movies_and_bins(errors):
    rows, bins, crosses = [], [], []
    for slice_name, subset in (("ALL", errors), ("H_POSITIVE", errors[errors.h.gt(0)]),
                               ("H_ZERO", errors[errors.h.eq(0)])):
        for (model, cap), data in subset.groupby(["model", "cap"], sort=True):
            groups = {"ALL": np.ones(len(data), bool), "C": data.blocked.to_numpy(bool),
                      "NATURAL_ZERO": (~data.blocked & data.support.eq(0)).to_numpy(),
                      "W_DIRECT": data.direct.to_numpy(bool)}
            for group, mask in groups.items():
                if model == "REFERENCE_ALS" and group != "W_DIRECT":
                    continue
                part = data[mask]
                for metric in ("native_se", "native_ae", "calibrated_se", "calibrated_ae"):
                    observed = part[part[metric].notna()]
                    per_movie = observed.groupby("movie_id")[metric].mean()
                    rows.append({**model_info(model), "model": model, "cap": cap, "h_group": slice_name,
                        "group": group, "metric": metric, "users": int(observed.uid.nunique()),
                        "movies": len(per_movie), "rows": len(observed),
                        "movie_macro": float(per_movie.mean()) if len(per_movie) else np.nan,
                        "row_micro": float(observed[metric].mean()) if len(observed) else np.nan})
                for kind in ("native", "calibrated"):
                    score = part[kind].to_numpy(float)
                    index = np.full(len(part), -1, dtype=int)
                    finite = np.isfinite(score)
                    index[finite] = np.minimum(8, np.floor((score[finite]-.5)*2)).astype(int)
                    for bucket in range(-1, 9):
                        z = part[index == bucket]
                        bins.append({**model_info(model), "model": model, "cap": cap, "h_group": slice_name,
                            "group": group, "prediction_kind": kind, "bin": bucket,
                            "lower": .5+.5*bucket if bucket >= 0 else None,
                            "upper": 1.+.5*bucket if bucket >= 0 else None,
                            "right_inclusive": bucket == 8, "users": int(z.uid.nunique()), "rows": len(z),
                            "mean_prediction": float(z[kind].mean()) if len(z) else np.nan,
                            "mean_rating": float(z.rating.mean()) if len(z) else np.nan})
            # Explicit support x metadata diagnostics; descriptive, never additional tests.
            fields = [f for f in data if f.startswith("MISSING_")] + ["new_metadata_category"]
            support_band = np.select([data.support.eq(0), data.support.between(1, 9), data.support.between(10, 49)],
                                     ["0", "1_9", "10_49"], default="50_PLUS")
            for field in fields:
                for band in ("0", "1_9", "10_49", "50_PLUS"):
                    for present in (False, True):
                        z = data[(support_band == band) & data[field].eq(present)]
                        if model == "REFERENCE_ALS":
                            z = z[z.direct]
                        for metric in ("native_se", "calibrated_se"):
                            good = z[z[metric].notna()]
                            user_mean = good.groupby("uid")[metric].mean()
                            crosses.append({**model_info(model), "model": model, "cap": cap,
                                "h_group": slice_name, "support_band": band, "metadata_condition": field,
                                "condition_value": present, "metric": metric, "users": len(user_mean),
                                "rows": len(good), "movies": int(good.movie_id.nunique()),
                                "user_macro": float(user_mean.mean()) if len(user_mean) else np.nan})
    return pd.DataFrame(rows), pd.DataFrame(bins), pd.DataFrame(crosses)


def personalization(frame):
    # W_DIRECT changes with ALS usable input; it cannot be an O-vs-empty fixed pool.
    used = frame[frame.group.isin(["ALL", "W", "C", "NATURAL_ZERO"]) & frame.model.ne("REFERENCE_ALS")]
    base = used[used.cap.eq(0)]
    rows = []
    metrics = ["native_mse", "calibrated_mse", "raw_mse", "ndcg2", "stars2", "low2"]
    for cap in CAPS[1:]:
        current = used[used.cap.eq(cap) & used.h.gt(0)]
        pair = current.merge(base, on=["uid", "model", "group"], suffixes=("_real", "_empty"), validate="one_to_one")
        require(len(pair) == len(current) and pair.targets_real.eq(pair.targets_empty).all(), "fixed O-vs-empty targets")
        for r in pair.itertuples():
            for metric in metrics:
                before, after = getattr(r, metric + "_empty"), getattr(r, metric + "_real")
                rows.append({**model_info(r.model), "model": r.model, "uid": r.uid, "cap": cap,
                    "h": r.h_real, "group": r.group, "metric": metric, "empty": before, "real": after,
                    "delta": after-before if np.isfinite(before) and np.isfinite(after) else np.nan,
                    "includes_different_cap_calibrator": metric == "calibrated_mse"})
    result = pd.DataFrame(rows)
    summary = []
    for keys, data in result.groupby(["model", "cap", "group", "metric"], sort=True):
        model, cap, group, metric = keys
        z = data[data.delta.notna()]
        summary.append({**model_info(model), "model": model, "cap": cap, "group": group, "metric": metric,
                        "users": len(data), "valid": len(z), "empty": float(z["empty"].mean()),
                        "real": float(z.real.mean()), "delta": float(z.delta.mean()),
                        "interpretation": "INPUT_AND_CAP_CALIBRATION" if metric == "calibrated_mse" else "INPUT_CONDITIONAL_ASSOCIATION"})
    return result, pd.DataFrame(summary)


def primary_statistics(frame, selection, outcomes, calibration, cfg):
    selected_ids = {f: [selection["selected"][f]+"_s"+str(s) for s in SEEDS] for f in FAMILIES}
    ready = {f: {s: outcomes[m]["status"] == "SUCCESS" and calibration.get((m, 10), {}).get("a") is not None
                  for s, m in zip(SEEDS, selected_ids[f])} for f in FAMILIES}
    common_seeds = [s for s in SEEDS if all(ready[f][s] for f in FAMILIES)]
    complete = common_seeds == list(SEEDS)
    used = frame[frame.cap.eq(10) & frame.h.gt(0) & frame.kind.eq("CANDIDATE")]
    contrasts, paired, seed_effects = [], [], []
    for group in ("ALL", "C"):
        for metric in ("calibrated_mse", "ndcg2"):
            sub = used[used.group.eq(group)]
            pivot = sub.pivot(index="uid", columns="model", values=metric).sort_index()
            seed_deltas = []
            for seed in common_seeds:
                before, after = [selection["selected"][f]+"_s"+str(seed) for f in FAMILIES]
                require(before in pivot and after in pivot, "both seed metric columns")
                pair = pivot[[before, after]]
                finite = np.isfinite(pair.to_numpy(float))
                require(np.array_equal(finite[:, 0], finite[:, 1]), "same per-seed metric denominator")
                pair = pair.loc[finite.all(axis=1)]
                delta = float((pair[after]-pair[before]).mean()) if len(pair) else None
                seed_effects.append({"seed": seed, "group": group, "metric": metric, "users": len(pair),
                    "before_mean": float(pair[before].mean()) if len(pair) else None,
                    "after_mean": float(pair[after].mean()) if len(pair) else None, "delta": delta})
                seed_deltas.append(delta)
            row = {"before": "FM", "after": "GBT", "group": group, "metric": metric,
                   "scope": "DEVELOPMENT_ONLY", "users": 0, "before_mean": None, "after_mean": None,
                   "delta": None, "ci_low": None, "ci_high": None, "confidence": cfg["primary_ci"],
                   "complete_paired_seeds": complete, "seed_direction_consistent": None}
            if complete:
                columns = selected_ids["FM"] + selected_ids["GBT"]
                require(set(columns).issubset(pivot.columns), "all six selected metric columns")
                pair = pivot[columns]
                finite = np.isfinite(pair.to_numpy(float))
                require(np.array_equal(finite.any(axis=1), finite.all(axis=1)), "metric eligibility fixed across every model and seed")
                pair = pair.loc[finite.all(axis=1)]
                means = {f: pair[selected_ids[f]].mean(axis=1) for f in FAMILIES}
                row.update(bootstrap_difference(means["FM"].to_numpy(), means["GBT"].to_numpy(),
                    samples=cfg["bootstrap_samples"], seed=cfg["bootstrap_seed"], confidence=cfg["primary_ci"], minimum=cfg["min_ci_users"]))
                if row["delta"] is not None:
                    row["seed_direction_consistent"] = bool(all(v is not None and np.sign(v) == np.sign(row["delta"]) for v in seed_deltas))
                for uid in pair.index:
                    paired.append({"uid": int(uid), "group": group, "metric": metric,
                                   "FM": float(means["FM"][uid]), "GBT": float(means["GBT"][uid]),
                                   "delta": float(means["GBT"][uid]-means["FM"][uid])})
            contrasts.append(row)
    decision = {"scope": "DEVELOPMENT_ONLY", "complete_paired_seeds": complete,
        "common_seeds": common_seeds, "ready": ready, "decision": four_way_decision(contrasts),
        "service_adoption": False, "fresh_test": False, "uncertainty": "CONDITIONAL_ON_FIXED_FITS_CALIBRATORS_AND_REUSED_DEVELOPMENT_USERS",
        "prediction_ensemble": False, "additional_search_authorized": False}
    return pd.DataFrame(contrasts), pd.DataFrame(paired, columns=["uid", "group", "metric", "FM", "GBT", "delta"]), pd.DataFrame(seed_effects), decision


def average_seed_metrics(frame, page=False):
    """Average metrics for one user, never scores. Requires all three seeds."""
    keys = ["uid", "cap", "group"] + (["end"] if page else [])
    constants = ["h", "pre_all", "actual_h", "activity"] + (["j", "returned"] if page else ["targets", "predicted"])
    identity = set(keys + constants + ["kind", "recipe", "family", "seed", "model"])
    numeric = [name for name in frame.columns if name not in identity]
    rows = []
    for family in FAMILIES:
        chosen = frame[frame.kind.eq("CANDIDATE") & frame.family.eq(family)]
        for _, group in chosen.groupby(keys, sort=True):
            require(sorted(group.seed.tolist()) == list(SEEDS), "three seeds for every averaged metric row")
            for column in constants:
                require(group[column].nunique(dropna=False) == 1, "fixed metric denominator across seeds")
            first = group.iloc[0]
            row = {key: first[key] for key in keys + constants}
            row.update(model="SEED_MEAN_" + family, **model_info("SEED_MEAN_" + family))
            for column in numeric:
                values = group[column].to_numpy(float)
                # Never silently average two calibrated values when the third is N/A.
                row[column] = float(values.mean()) if np.isfinite(values).all() else np.nan
            rows.append(row)
    return pd.DataFrame(rows, columns=frame.columns)


def evaluate():
    a, cfg = api(), config()
    selection_parent = snapshot_parent("selection-seal.json")
    fit_ids, selection = selected_fit_ids()
    files = ["user-metrics.parquet", "page-metrics.parquet", "rating-errors.parquet", "comparison-profiles.csv",
             "summary.csv", "page-summary.csv", "movie-metrics.csv", "calibration-bins.csv",
             "support-metadata-metrics.csv", "personalization-users.parquet", "personalization-summary.csv",
             "primary-contrasts.csv", "primary-paired-users.parquet", "seed-effects.csv", "decision.json",
             "seed-mean-user-metrics.parquet", "seed-mean-page-metrics.parquet", "seed-mean-summary.csv", "seed-mean-page-summary.csv"]
    preserve(files + ["evaluation-seal.json"])
    started, outcomes = gate(fit_ids)
    calibration_parent = snapshot_parent("final-calibration-seal.json")
    calibration, parent = calibration_map("final")
    require(parent["evaluation_inputs"] == started, "same final calibration gate")
    require(parent["selection_seal"] == selection_parent, "same frozen recipe selection")
    t = time.monotonic()
    catalog, contexts, roles, labels, predictions = load_data(outcomes)
    old_values, direct, old_calibration, old_parents = references(max(c["stop"] for c in contexts))
    predictions.update(old_values)
    calibration.update(old_calibration)
    masks, movie_info = metadata_masks(catalog)
    users, pages, errors, profiles = build_metrics(catalog, contexts, roles, labels, predictions, calibration, direct, masks, movie_info)
    summary, page_summary = summarize_users(users, pages)
    movies, bins, support_metadata = summarize_movies_and_bins(errors)
    personal_users, personal_summary = personalization(users)
    contrasts, paired, seed_effects, decision = primary_statistics(users, selection, outcomes, calibration, cfg)
    decision["resource_status"] = {k: v["resource_status"] for k, v in outcomes.items()}
    decision["strict_resources_all_pass"] = all(v["resource_status"] == "PASS" for v in outcomes.values())
    if decision["complete_paired_seeds"]:
        mean_users = average_seed_metrics(users)
        mean_pages = average_seed_metrics(pages, page=True)
        mean_summary, mean_page_summary = summarize_users(mean_users, mean_pages)
    else:
        mean_users, mean_pages = users.iloc[:0], pages.iloc[:0]
        mean_summary, mean_page_summary = summary.iloc[:0], page_summary.iloc[:0]
    for name, frame in (("user-metrics.parquet", users), ("page-metrics.parquet", pages), ("rating-errors.parquet", errors),
                        ("personalization-users.parquet", personal_users), ("primary-paired-users.parquet", paired),
                        ("seed-mean-user-metrics.parquet", mean_users), ("seed-mean-page-metrics.parquet", mean_pages)):
        frame.to_parquet(a.OUT / name, index=False)
    for name, frame in (("comparison-profiles.csv", profiles), ("summary.csv", summary), ("page-summary.csv", page_summary),
                        ("movie-metrics.csv", movies), ("calibration-bins.csv", bins),
                        ("support-metadata-metrics.csv", support_metadata), ("personalization-summary.csv", personal_summary),
                        ("primary-contrasts.csv", contrasts), ("seed-effects.csv", seed_effects),
                        ("seed-mean-summary.csv", mean_summary), ("seed-mean-page-summary.csv", mean_page_summary)):
        frame.to_csv(a.OUT / name, index=False)
    a.write_json(a.OUT / "decision.json", decision)
    require(started == gate(fit_ids)[0], "final evaluation inputs/code drift")
    verify_parent_snapshot("final-calibration-seal.json", calibration_parent)
    verify_parent_snapshot("selection-seal.json", selection_parent)
    for name, expected in old_parents.items():
        require(a.pin(a.ROOT / name) == expected, "old descriptive parents unchanged")
    a.seal("evaluation-seal.json", files, execution=started["execution"], evaluation_inputs=started,
           selection_seal=selection_parent,
           calibration_seal=calibration_parent,
           descriptive_reference_parents=old_parents, seconds=time.monotonic()-t)
    print("FINAL344_EVALUATED", decision["decision"], round(time.monotonic()-t, 2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["calibrate", "select", "calibrate-final", "evaluate"])
    action = parser.parse_args().action
    if action in {"calibrate", "calibrate-final"}:
        calibrate(final=action == "calibrate-final")
    elif action == "select":
        select()
    else:
        evaluate()
