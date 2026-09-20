"""Evaluate FM-v6 exact prefix-K predictions on sealed VALIDATION labels."""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from fm_v2_train import file_pin
from fm_v5_train import FACTOR_PROFILES, LINEAR_PROFILES
from fm_zero_n_prepare import KeyDigest


KEYS = ["uid", "target_movie_id", "prediction_at", "candidate_movie_id", "candidate_rank"]
CUTOFFS = (2, 4, 6, 10)
BASELINES = ("popularity_bayes_v5", "content_linear_v5")


def jsonl_frame(path: Path) -> pd.DataFrame:
    # prediction_at is a Unix-second integer identity key, not a datetime.
    return pd.read_json(path, lines=True, convert_dates=False)


def parquet_key_digest(path: Path) -> dict:
    result = KeyDigest()
    for batch in pq.ParquetFile(path).iter_batches(columns=["episode_id", "candidate_movie_id"]):
        for episode_id, movie_id in zip(batch.column(0).to_pylist(), batch.column(1).to_pylist()):
            result.update((episode_id, movie_id))
    return result.document()


def genre_diversity(values: list[str]) -> float:
    sets = [set(str(value).split("|")) - {"", "(no genres listed)"} for value in values]
    pairs = list(combinations(sets, 2))
    return float(np.mean([not bool(left & right) for left, right in pairs])) if pairs else float("nan")


def contributions(frame: pd.DataFrame, score_name: str, output_name: str) -> pd.DataFrame:
    rows = []
    for episode_id, group in frame.groupby("episode_id", sort=False):
        ranked = group.sort_values([score_name, "candidate_movie_id"], ascending=[False, True], kind="stable")
        observed = ranked.observed_rating.notna()
        ideal_ratings = np.sort(ranked.loc[observed, "observed_rating"].to_numpy(np.float64))[::-1]
        positive_total = int((ranked.label_state == "POSITIVE_OBSERVED").sum())
        first = ranked.iloc[0]
        item = {"episode_id": episode_id, "uid": int(first.uid), "target_movie_id": int(first.target_movie_id),
                "prediction_at": int(first.prediction_at), "n": int(first.n), "n_bucket": str(first.n_bucket),
                "profile": output_name, "candidate_rows": len(ranked),
                "observed_judgments": int(observed.sum()), "pool_observed_recall": 1.0,
                "pool_support": float(ranked.supported.mean())}
        if len(ideal_ratings) != 10:
            raise RuntimeError(f"NDCG@10 contract violated for {episode_id}: {len(ideal_ratings)} judgments")
        for cutoff in CUTOFFS:
            top = ranked.iloc[:cutoff]
            top_observed = top.observed_rating.notna().to_numpy()
            ratings = top.observed_rating.fillna(0.0).to_numpy(np.float64)
            gains = np.where(top_observed, np.power(2.0, ratings - 0.5) - 1.0, 0.0)
            dcg = float(np.sum(gains / np.log2(np.arange(cutoff) + 2.0)))
            ideal = ideal_ratings[:cutoff]
            idcg = float(np.sum((np.power(2.0, ideal - 0.5) - 1.0) /
                                np.log2(np.arange(cutoff) + 2.0)))
            if not np.isfinite(idcg):
                raise RuntimeError(f"non-finite IDCG is forbidden by contract: {episode_id}@{cutoff}")
            # All-ten-at-minimum-rating episodes have no positive gain under the
            # frozen gain transform. Keep the user in every paired comparison and
            # assign the same conventional zero score to every model/policy.
            item[f"zero_idcg_at_{cutoff}"] = bool(idcg == 0.0)
            item[f"ndcg_at_{cutoff}"] = 0.0 if idcg == 0.0 else dcg / idcg
            item[f"positive_recall_at_{cutoff}"] = (float((top.label_state == "POSITIVE_OBSERVED").sum()) /
                                                     positive_total if positive_total else np.nan)
            # UNKNOWN contributes zero low-rating exposure but remains explicitly unknown,
            # never a negative label.  The denominator is the fixed displayed cutoff.
            item[f"low_exposure_at_{cutoff}"] = float(
                ((top.observed_rating.notna()) & (top.observed_rating <= 2.0)).sum() / cutoff
            )
            item[f"support_at_{cutoff}"] = float(top.supported.mean())
            item[f"diversity_at_{cutoff}"] = genre_diversity(top.candidate_genres.fillna("").tolist())
        rows.append(item)
    return pd.DataFrame(rows)


def summary(frame: pd.DataFrame) -> dict:
    result = {"episodes": len(frame), "users": int(frame.uid.nunique()), "by_k": {}}
    for k, group in frame.groupby("n", sort=True):
        metrics = {}
        for cutoff in CUTOFFS:
            for stem in ("ndcg", "positive_recall", "low_exposure", "support", "diversity"):
                name = f"{stem}_at_{cutoff}"
                values = group.groupby("uid")[name].mean()
                metrics[name] = {"value": float(values.mean()) if len(values) else None,
                                 "users": int(values.notna().sum()),
                                 "excluded_users": int(values.isna().sum())}
            zero = group.groupby("uid")[f"zero_idcg_at_{cutoff}"].max()
            metrics[f"zero_idcg_at_{cutoff}"] = {
                "value": int(zero.sum()), "users": int(len(zero)), "excluded_users": 0,
            }
        result["by_k"][str(int(k))] = {"episodes": len(group), "users": int(group.uid.nunique()),
                                         "metrics": metrics}
    return result


def paired(left: pd.DataFrame, right: pd.DataFrame, metric: str, seed: int, replicates: int) -> dict:
    a = left.groupby("uid", sort=True)[metric].mean()
    b = right.groupby("uid", sort=True)[metric].mean()
    if not a.index.equals(b.index):
        raise RuntimeError(f"paired user sets differ for {metric}")
    joined = pd.concat((a.rename("a"), b.rename("b")), axis=1)
    if joined.isna().any().any() or not np.isfinite(joined.to_numpy(np.float64)).all():
        raise RuntimeError(f"paired gated metric has missing/non-finite values: {metric}")
    delta = (joined.a - joined.b).to_numpy(np.float64)
    if not len(delta):
        return {"status": "INSUFFICIENT_SAMPLE", "users": 0, "delta": None, "ci95": [None, None]}
    rng = np.random.default_rng(seed)
    draws = np.empty(replicates, dtype=np.float64)
    for offset in range(0, replicates, 250):
        width = min(250, replicates - offset)
        samples = rng.integers(0, len(delta), size=(width, len(delta)))
        draws[offset:offset + width] = delta[samples].mean(axis=1)
    return {"status": "PASS", "users": len(delta), "delta": float(delta.mean()),
            "ci95": [float(value) for value in np.quantile(draws, (0.025, 0.975))],
            "worse_user_fraction": float(np.mean(delta < 0.0)),
            "bootstrap": {"unit": "USER", "seed": seed, "replicates": replicates}}


def comparisons(model: pd.DataFrame, bases: dict[str, pd.DataFrame], config: dict) -> dict:
    seed, reps = int(config["uncertainty"]["bootstrap_seed"]), int(config["uncertainty"]["bootstrap_replicates"])
    return {base: {metric: paired(model, value, metric, seed, reps)
                   for metric in ("ndcg_at_2", "ndcg_at_4", "ndcg_at_6", "ndcg_at_10",
                                  "low_exposure_at_2", "diversity_at_2", "pool_observed_recall",
                                  "pool_support")}
            for base, value in bases.items()}


def safety_pass(metrics: dict) -> bool:
    return (metrics["ndcg_at_2"]["ci95"][0] is not None and metrics["ndcg_at_2"]["ci95"][0] > 0.0 and
            metrics["ndcg_at_2"].get("worse_user_fraction", 1.0) < 0.5 and
            metrics["low_exposure_at_2"]["ci95"][1] is not None and
            metrics["low_exposure_at_2"]["ci95"][1] <= 0.02 and
            metrics["diversity_at_2"]["delta"] is not None and metrics["diversity_at_2"]["delta"] >= 0.0 and
            metrics["diversity_at_2"]["ci95"][0] is not None and
            metrics["diversity_at_2"]["ci95"][0] >= -0.02 and
            metrics["pool_observed_recall"]["delta"] == 0.0 and metrics["pool_support"]["delta"] == 0.0 and
            all(metrics[f"ndcg_at_{cutoff}"]["delta"] is not None and
                metrics[f"ndcg_at_{cutoff}"]["delta"] > 0.0 for cutoff in (4, 6, 10)))


def evaluate(args: argparse.Namespace) -> dict:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    fit_path, prepared_path = args.fits_root / "run-report.json", args.prepared_root / "manifest.json"
    fit = json.loads(fit_path.read_text(encoding="utf-8"))
    prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
    labels_manifest_path = args.labels_root / "manifest.json"
    labels_manifest = json.loads(labels_manifest_path.read_text(encoding="utf-8"))
    if (fit.get("status") != "PASS" or fit.get("validation_labels_read") is not False or
            fit.get("final_test_opened") is not False or fit.get("prepared_manifest") != file_pin(prepared_path)):
        raise RuntimeError("fit/prepared linkage failed")
    if (labels_manifest.get("status") != "SEALED_VALIDATION_LABELS" or
            labels_manifest.get("source_feature_manifest") != prepared.get("isolated_input_manifest") or
            labels_manifest.get("final_test") != "NOT_PRESENT"):
        raise RuntimeError("label isolation linkage failed")
    for name, expected in labels_manifest["files"].items():
        if file_pin(args.labels_root / name) != expected:
            raise RuntimeError(f"label file pin mismatch: {name}")
    prediction_path = args.fits_root / "validation-candidate-predictions.parquet"
    target_prediction_path = args.fits_root / "validation-target-predictions.parquet"
    if (file_pin(prediction_path) != fit["artifacts"]["candidate_predictions"] or
            file_pin(target_prediction_path) != fit["artifacts"]["target_predictions"]):
        raise RuntimeError("prediction pin mismatch")
    prepared_prediction_contracts = (
        (prediction_path, args.prepared_root / "validation-candidates.parquet", "validation_candidates"),
        (target_prediction_path, args.prepared_root / "validation-targets.parquet", "validation_targets"),
    )
    for actual, source_table, manifest_key in prepared_prediction_contracts:
        if (file_pin(source_table) != prepared["files"][source_table.name] or
                pq.ParquetFile(actual).metadata.num_rows != prepared["rows"][manifest_key] or
                parquet_key_digest(actual) != prepared["row_key_multisets"][manifest_key] or
                parquet_key_digest(source_table) != prepared["row_key_multisets"][manifest_key]):
            raise RuntimeError(f"prediction/prepared row-key contract failed: {manifest_key}")
    profile_names = ["popularity_bayes_v5", *LINEAR_PROFILES, *FACTOR_PROFILES]
    schema_names = pq.ParquetFile(prediction_path).schema_arrow.names
    seed_names = sorted(name for name in schema_names if "__seed_" in name)
    expected_seed_names = sorted(f"{profile}__seed_{seed}" for profile in FACTOR_PROFILES
                                 for seed in config["model"]["model_seeds"])
    if seed_names != expected_seed_names:
        raise RuntimeError("seed/profile prediction matrix mismatch")
    prediction_columns = ["episode_id", *KEYS, "n", "n_bucket", "supported", "candidate_genres",
                          *profile_names, *seed_names]
    prediction = pq.read_table(prediction_path, columns=prediction_columns).to_pandas()
    base_labels = jsonl_frame(args.labels_root / "validation-candidate-labels-base.jsonl")
    candidate = prediction.merge(base_labels, on=KEYS, how="left", validate="many_to_one", sort=False)
    if candidate.label_state.isna().any() or len(candidate) != len(prediction):
        raise RuntimeError("candidate prediction/base-label join failed")
    sizes = candidate.groupby("episode_id", sort=False).size()
    if not (sizes == int(config["input"]["candidate_count"])).all():
        raise RuntimeError("candidate prediction does not contain exactly 50 rows per episode")
    expected_k = set(map(int, config["input"]["k_values"]))
    if any(set(map(int, values)) != expected_k for values in candidate.groupby("uid", sort=False).n.unique()):
        raise RuntimeError("candidate predictions do not contain every exact-K episode per prepared user")
    contrib = {name: contributions(candidate, name, name) for name in profile_names}
    seed_contrib = {name: contributions(candidate, name, name) for name in seed_names}
    summaries = {name: summary(value) for name, value in contrib.items()}
    bases = {name: contrib[name] for name in BASELINES}
    fm = str(config["policy"]["selected_fm_profile"])
    if fm not in FACTOR_PROFILES:
        raise RuntimeError("configured policy FM profile is not a fitted factor profile")
    by_k = {}
    for k in config["input"]["k_values"]:
        model = contrib[fm][contrib[fm].n == k]
        k_bases = {name: value[value.n == k] for name, value in bases.items()}
        evidence = comparisons(model, k_bases, config)
        seeds = {base: [paired(seed_contrib[f"{fm}__seed_{seed}"][lambda x: x.n == k], value,
                                      "ndcg_at_2", 339, 1000)["delta"]
                        for seed in config["model"]["model_seeds"]]
                 for base, value in k_bases.items()}
        by_k[str(k)] = {"comparisons": evidence, "seed_deltas": seeds,
                        "strict_pass": all(safety_pass(value) for value in evidence.values()) and
                                       all(delta is not None and delta > 0.0
                                           for values in seeds.values() for delta in values)}
    thresholds = {}
    selected_policy = None
    n_values = candidate.n.to_numpy(np.float64)
    fm_scores = candidate[fm].to_numpy(np.float64)
    policy_contrib = {}
    for threshold in config["policy"]["thresholds"]:
        stable_ks = [k for k in config["input"]["k_values"] if k >= threshold]
        region_pass = bool(stable_ks) and all(by_k[str(k)]["strict_pass"] for k in stable_ks)
        thresholds[str(threshold)] = {"stable_k": stable_ks, "region_pass": region_pass}
        for baseline in BASELINES:
            base_scores = candidate[baseline].to_numpy(np.float64)
            alpha = np.where(n_values == 0, 0.0, np.minimum(1.0, n_values / float(threshold)))
            for form, scores in (
                ("hard", np.where(n_values < threshold, base_scores, fm_scores)),
                ("blend", (1.0 - alpha) * base_scores + alpha * fm_scores),
            ):
                name = f"{form}_{baseline}_t{threshold}"
                policy_contrib[name] = contributions(candidate.assign(**{name: scores}), name, name)
        if selected_policy is None and region_pass:
            # Choose the hard-switch baseline with the larger lower CI against the other baseline.
            candidates = [f"hard_{baseline}_t{threshold}" for baseline in BASELINES]
            policy_checks = {name: comparisons(policy_contrib[name], bases, config) for name in candidates}
            eligible = [name for name, values in policy_checks.items()
                        if all(safety_pass(value) for value in values.values())]
            if eligible:
                selected_policy = max(eligible, key=lambda name: min(
                    policy_checks[name][base]["ndcg_at_2"]["ci95"][0] for base in BASELINES
                ))
    policy_evidence = {name: comparisons(value, bases, config) for name, value in policy_contrib.items()}
    summaries.update({name: summary(value) for name, value in policy_contrib.items()})
    if selected_policy is None:
        eligible = []
        for name, values in policy_evidence.items():
            if not name.startswith("blend_"):
                continue
            threshold = name.rsplit("t", 1)[1]
            if thresholds[threshold]["region_pass"] and all(safety_pass(value) for value in values.values()):
                eligible.append(name)
        if eligible:
            selected_policy = max(eligible, key=lambda name: min(
                policy_evidence[name][base]["ndcg_at_2"]["ci95"][0] for base in BASELINES
            ))
    interactions = {}
    for profile, matched in config["interaction_comparisons"].items():
        evidence = comparisons(contrib[profile], {matched: contrib[matched]}, config)[matched]
        seeds = [paired(seed_contrib[f"{profile}__seed_{seed}"], contrib[matched], "ndcg_at_2", 339, 1000)["delta"]
                 for seed in config["model"]["model_seeds"]]
        interactions[profile] = {"matched_linear": matched, "evidence": evidence, "seed_deltas": seeds,
                                 "strict_pass": safety_pass(evidence) and
                                                all(value is not None and value > 0 for value in seeds)}
    target_prediction = pq.read_table(target_prediction_path).to_pandas()
    target_labels = jsonl_frame(args.labels_root / "validation-target-labels.jsonl")
    target = target_prediction.merge(target_labels, on=["episode_id", "uid", "target_movie_id", "prediction_at", "n"],
                                     how="left", validate="one_to_one", indicator=True)
    if (len(target) != prepared["rows"]["validation_targets"] or
            not (target["_merge"] == "both").all() or target.observed_rating.isna().any() or
            any(set(map(int, values)) != expected_k
                for values in target.groupby("uid", sort=False).n.unique())):
        raise RuntimeError("target prediction/label join is incomplete or lacks an exact-K user matrix")
    target = target.drop(columns=["_merge"])
    target_errors = {}
    for profile in profile_names:
        error = target[profile] - target.observed_rating
        target_errors[profile] = {"user_macro_mse": float(error.pow(2).groupby(target.uid).mean().mean()),
                                  "user_macro_mae": float(error.abs().groupby(target.uid).mean().mean()),
                                  "rows": len(target), "users": int(target.uid.nunique())}
    selected = policy_contrib[selected_policy] if selected_policy else contrib[fm]
    contribution_path = args.output_root / "selected-policy-contributions.parquet"
    pq.write_table(pa.Table.from_pandas(selected, preserve_index=False), contribution_path, compression="zstd")
    report = {
        "schema_version": 6, "status": "PASS", "claim_ceiling": config["claim_ceiling"],
        "K_POLICY_STATUS": "CANDIDATE" if selected_policy else "NOT_SUPPORTED",
        "TRANSITION_THRESHOLD_STATUS": "VALIDATION_REQUIRED" if selected_policy else "NOT_EVALUATED",
        "FM_INTERACTION_STATUS": "CANDIDATE" if interactions[fm]["strict_pass"] else "NOT_SUPPORTED",
        "selected_policy": selected_policy, "summaries": summaries, "exact_k_evidence": by_k,
        "thresholds": thresholds, "policy_evidence": policy_evidence, "interaction_evidence": interactions,
        "target_error_metrics_secondary": target_errors,
        "interpretation_limit": "K=0..2 are early prefixes of established users, not genuine new-user validation",
        "artifacts": {"fit_report": file_pin(fit_path), "prepared_manifest": file_pin(prepared_path),
                      "config": file_pin(args.config), "evaluator": file_pin(Path(__file__)),
                      "labels_manifest": file_pin(labels_manifest_path),
                      "candidate_predictions": file_pin(prediction_path),
                      "candidate_labels": file_pin(args.labels_root / "validation-candidate-labels-base.jsonl"),
                      "target_predictions": file_pin(target_prediction_path),
                      "target_labels": file_pin(args.labels_root / "validation-target-labels.jsonl"),
                      "selected_contributions": file_pin(contribution_path)},
        "validation_labels_opened": True, "final_test_opened": False,
    }
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "K_POLICY_STATUS", "TRANSITION_THRESHOLD_STATUS",
                                                   "FM_INTERACTION_STATUS", "selected_policy")}, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--fits-root", type=Path, required=True)
    parser.add_argument("--labels-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    if not args.output_root.is_dir() or any(args.output_root.iterdir()):
        raise FileExistsError("evaluation output must be an existing empty directory")
    evaluate(args)


if __name__ == "__main__":
    main()
