"""Verify the complete Jira 622 evidence bundle without opening FINAL_TEST."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "experiments" / "gbt-zero-n" / "completion-config.json"
PROMOTION_POLICY = ROOT / "experiments" / "gbt-zero-n" / "promotion-policy.json"


def pin(path: Path) -> dict[str, int | str]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def promotion_decision(validation: dict, sensitivity: dict, rank: dict, policy: dict) -> dict:
    selected = validation["selected_profile_on_validation"]
    selected_metrics = validation["profiles"][selected]
    low_users = {
        row["total_history_bucket"]: row["users"]
        for row in selected_metrics["low_history_cohort"]
    }
    rank_ci = rank["paired_user_macro_ndcg_at_10_delta_bootstrap_95_ci"]
    rank_positive = rank["paired_user_macro_ndcg_at_10_delta"] > 0 and rank_ci[0] > 0
    seed_stable = bool(sensitivity["all_seeds_pass_nested_point_estimate_gate"])
    unknown_fraction = selected_metrics["candidate_metrics"]["unknown_slot_fraction_at_10"]["value"]

    gates = {
        "validation_selection_gate": selected in validation["selection_gate"]["eligible_profiles"],
        "all_seeds_nested_non_worse": seed_stable,
        "pairwise_rank_positive_ci": rank_positive,
    }
    required_gates = [
        name for name, policy_key in (
            ("validation_selection_gate", "require_validation_selection_gate"),
            ("all_seeds_nested_non_worse", "require_all_seeds_nested_non_worse"),
            ("pairwise_rank_positive_ci", "require_pairwise_rank_positive_ci"),
        ) if policy[policy_key]
    ]
    reasons = []
    if not seed_stable:
        reasons.append("selected regression profile is not non-worse across N for every tested seed")
    if not rank_positive:
        reasons.append("pairwise rank objective does not improve observed NDCG@10")
    limitations = [
        f"full-history N=1/2 cohorts contain {low_users.get('1', 0)} and {low_users.get('2', 0)} users",
        f"selected-profile unjudged top-10 fraction is {unknown_fraction:.6f}",
    ]
    return {
        "decision": "PROMOTE_TO_FINAL_TEST" if all(gates[name] for name in required_gates)
        else "DO_NOT_PROMOTE",
        "selected_validation_profile": selected,
        "gates": gates,
        "required_gates": required_gates,
        "reasons": reasons,
        "limitations": limitations,
        "observed": {
            "seed_mse_range": sensitivity["user_macro_mse_range"],
            "seed_mse_span": sensitivity["user_macro_mse_span"],
            "pairwise_rank_ndcg_delta": rank["paired_user_macro_ndcg_at_10_delta"],
            "pairwise_rank_ndcg_delta_95_ci": rank_ci,
            "low_history_users": low_users,
            "selected_profile_unknown_top10_fraction": unknown_fraction,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-manifest", type=Path, required=True)
    parser.add_argument("--base-verification", type=Path, required=True)
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--sensitivity-report", type=Path, required=True)
    parser.add_argument("--rank-comparison", type=Path, required=True)
    parser.add_argument("--rank-metrics", type=Path, required=True)
    parser.add_argument("--legacy-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")

    config = load(CONFIG)
    promotion_policy = load(PROMOTION_POLICY)
    documents = {
        "base_verification": load(args.base_verification),
        "validation_report": load(args.validation_report),
        "sensitivity_report": load(args.sensitivity_report),
        "rank_comparison": load(args.rank_comparison),
        "legacy_audit": load(args.legacy_audit),
    }
    checks: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(message)
        checks.append(message)

    for name, document in documents.items():
        require(document["status"] == "PASS", f"{name} status is PASS")
        require(document["final_test_opened"] is False, f"{name} keeps FINAL_TEST sealed")

    verification = documents["base_verification"]
    validation = documents["validation_report"]
    sensitivity = documents["sensitivity_report"]
    rank = documents["rank_comparison"]
    rank_metrics = load(args.rank_metrics)
    legacy = documents["legacy_audit"]
    prepared_pin = pin(args.prepared_manifest)

    require(verification["selected_profile_on_validation"] == config["sensitivity_profile"],
            "base verification selected the configured sensitivity profile")
    require(all(value == "PASS" for value in validation["requirements_coverage"].values()),
            "arbitrary-N, low-history, nested-history, and candidate denominator coverage passed")
    require(sensitivity["profile"] == config["sensitivity_profile"],
            "sensitivity report used the selected validation profile")
    require([run["seed"] for run in sensitivity["seeds"]] ==
            [config["base_seed"], *config["sensitivity_seeds"]],
            "sensitivity report contains the configured seeds")
    require(sensitivity["prepared_manifest"] == prepared_pin,
            "sensitivity report pins the exact prepared manifest")
    require(sensitivity["completion_config"] == pin(CONFIG),
            "sensitivity report pins the exact completion config")
    require(rank["same_candidate_identity"] is True,
            "rank and regression objectives used identical validation candidates")
    require(rank["rank_metrics"] == pin(args.rank_metrics),
            "rank comparison pins the exact rank metrics")
    require("rank_predictions" in rank and "sha256" in rank["rank_predictions"],
            "rank comparison pins the exact rank predictions")
    require(rank_metrics["status"] == "PASS" and rank_metrics["final_test_opened"] is False,
            "rank metrics passed and kept FINAL_TEST sealed")
    require(rank_metrics["completion_config"] == pin(CONFIG),
            "rank metrics pin the exact completion config")
    require(rank_metrics["worker"] == pin(ROOT / "scripts" / "gbt_zero_n_rank_worker.py"),
            "rank metrics pin the current rank worker")
    require(legacy["fit_id"] == config["legacy_baseline"]["fit_id"],
            "legacy audit identifies the configured baseline")
    require(legacy["comparison_policy"] == config["legacy_baseline"]["comparison_policy"] and
            legacy["eligible_for_v7_same_cohort_comparison"] is False,
            "legacy baseline remains audit-only and non-comparable")

    decision = promotion_decision(validation, sensitivity, rank, promotion_policy)
    report = {
        "schema_version": 1,
        "status": "PASS",
        "evidence_status": "COMPLETE",
        "experiment": config["experiment"],
        "checks": checks,
        "promotion": decision,
        "artifacts": {
            name: pin(path) for name, path in {
                "prepared_manifest": args.prepared_manifest,
                "base_verification": args.base_verification,
                "validation_report": args.validation_report,
                "sensitivity_report": args.sensitivity_report,
                "rank_comparison": args.rank_comparison,
                "rank_metrics": args.rank_metrics,
                "legacy_audit": args.legacy_audit,
                "completion_config": CONFIG,
                "promotion_policy": PROMOTION_POLICY,
                "completion_verifier": Path(__file__),
            }.items()
        },
        "final_test_opened": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
