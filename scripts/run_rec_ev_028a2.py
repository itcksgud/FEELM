"""Fail-closed REC-EV-028A2 pre-label runner.

The numerical implementation is inherited from the audited A1 runner.  This
entry point adds the A2 global target/profile firewall plus semantic recursive
lock and resume validation.  It intentionally has no label-opening phase.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from build_rec_ev_019b_features import sha256_file
from preflight_rec_ev_028a1_membership import _coverage_truth
from preflight_rec_ev_028a_membership import OUTERS
from rec_ev_027_core import deterministic_order
from run_rec_ev_028a1 import (
    CELLS,
    SYSTEMS,
    ResumeError,
    artifact,
    fit_all,
    global_paths,
    outer_paths,
    prepare_all,
    score_all,
    verify_artifact,
)
from validate_rec_ev_028a2_amendment import DEFAULT as AMENDMENT_DEFAULT
from validate_rec_ev_028a2_amendment import ROOT, validate as validate_amendment


def load_contracts(amendment_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    validate_amendment(amendment, amendment_path.resolve())
    base = json.loads((ROOT / amendment["base_contract"]["path"]).read_text(encoding="utf-8"))
    a1 = json.loads((ROOT / amendment["a1_amendment"]["path"]).read_text(encoding="utf-8"))
    parent = json.loads((ROOT / base["inputs"]["parent_contract"]["path"]).read_text(encoding="utf-8"))
    return amendment, base, a1, parent


def _compare_number(actual: Any, expected: Any, label: str) -> None:
    if isinstance(expected, float):
        if not np.isclose(float(actual), expected, rtol=0.0, atol=1e-12):
            raise RuntimeError(f"{label} drift")
    elif actual != expected:
        raise RuntimeError(f"{label} drift")


def _strict_int_list(value: Any, label: str) -> list[int]:
    if not isinstance(value, (list, tuple, np.ndarray)):
        raise ResumeError(f"{label} is not a list-like integer payload")
    result: list[int] = []
    for item in value:
        if isinstance(item, (bool, np.bool_)) or not isinstance(item, (int, np.integer)):
            raise ResumeError(f"{label} contains a non-integer")
        result.append(int(item))
    return result


def _strict_float_list(value: Any, label: str) -> list[float]:
    if not isinstance(value, (list, tuple, np.ndarray)):
        raise ResumeError(f"{label} is not a list-like numeric payload")
    result: list[float] = []
    for item in value:
        if isinstance(item, (bool, np.bool_)) or not isinstance(item, (int, float, np.integer, np.floating)):
            raise ResumeError(f"{label} contains a non-numeric score")
        result.append(float(item))
    return result


def validate_membership_and_summary(
    amendment: dict[str, Any], base: dict[str, Any], output_root: Path
) -> tuple[pd.DataFrame, dict[str, Any]]:
    expected_root = (ROOT / amendment["output_root"]).resolve()
    if output_root.resolve() != expected_root:
        raise RuntimeError("output root differs from audited REC-EV-028A2 root")
    summary_path = output_root / "membership-preflight.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    required = {
        "status": "GLOBAL_LABEL_FIREWALL_MEMBERSHIP_PASS",
        "all_outer_coverage_gates_pass": True,
        "global_profile_target_intersection_pairs": 0,
        "rating_values_parsed": False,
        "timestamps_parsed": False,
        "future_membership_materialized": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    for key, expected in required.items():
        if summary.get(key) != expected:
            raise RuntimeError(f"membership summary firewall drift: {key}")
    if verify_artifact(summary["amendment"]).resolve() != AMENDMENT_DEFAULT.resolve():
        raise RuntimeError("membership summary amendment path drift")
    membership_path = verify_artifact(summary["membership"])
    expected_membership = (output_root / "cache/membership.parquet").resolve()
    if membership_path.resolve() != expected_membership:
        raise RuntimeError("membership summary path differs from runner input")
    expected_dependencies = {
        "scripts/preflight_rec_ev_028a2_membership.py",
        "scripts/preflight_rec_ev_028a1_membership.py",
        "scripts/preflight_rec_ev_028a_membership.py",
        "scripts/preflight_rec_ev_027_membership.py",
        "scripts/rec_ev_022a_core.py",
        "scripts/build_rec_ev_019b_features.py",
        "scripts/validate_rec_ev_028a2_amendment.py",
        "scripts/validate_rec_ev_028a1_amendment.py",
        "scripts/validate_rec_ev_028a_contract.py",
    }
    dependency_specs = summary.get("transitive_implementations", [])
    if {str(spec.get("path")) for spec in dependency_specs} != expected_dependencies:
        raise RuntimeError("transitive implementation set drift")
    for spec in dependency_specs:
        verify_artifact(spec)

    frame = pd.read_parquet(membership_path).sort_values(["outer", "user_key"], kind="stable", ignore_index=True)
    expected_outers = [slug for slug, _, _ in OUTERS]
    if sorted(frame["outer"].unique().tolist()) != sorted(expected_outers):
        raise RuntimeError("membership canonical outer set drift")
    if frame.duplicated(["outer", "user_key"]).any():
        raise RuntimeError("duplicate outer user")
    global_profiles: dict[str, set[int]] = {}
    a1_membership = pd.read_parquet(
        ROOT / amendment["a1_balanced_membership"]["membership"]["path"],
        columns=["outer", "user_key", "target_movie_ids"],
    )
    a1_targets: dict[str, set[int]] = {}
    a1_targets_by_row: dict[tuple[str, str], list[int]] = {}
    for row in a1_membership.itertuples(index=False):
        key = str(row.user_key)
        targets = list(map(int, row.target_movie_ids))
        a1_targets.setdefault(key, set()).update(targets)
        a1_targets_by_row[(str(row.outer), key)] = targets
    recomputed_coverage: dict[str, dict[str, Any]] = {}
    for slug, _, _ in OUTERS:
        group = frame.loc[frame["outer"].eq(slug)].copy()
        keys = group["user_key"].astype(str).tolist()
        donors = group["donor_user_key"].astype(str).tolist()
        expected_donors = keys[1:] + keys[:1]
        if donors != expected_donors or len(set(donors)) != len(donors):
            raise RuntimeError(f"donor bijection/derangement drift: {slug}")
        target_n = 20 if slug in {"R0", "R1", "R2", "R3", "R4"} else 4
        expected_track, expected_fold = next((track, fold) for outer, track, fold in OUTERS if outer == slug)
        for row in group.itertuples(index=False):
            key = str(row.user_key)
            if str(row.track) != expected_track or str(row.fold_or_domain) != expected_fold:
                raise RuntimeError(f"membership track/fold drift: {slug}")
            profile_values = _strict_int_list(row.profile_movie_ids, f"membership profile {slug} {key}")
            target_values = _strict_int_list(row.target_movie_ids, f"membership target {slug} {key}")
            profile = set(profile_values)
            target = set(target_values)
            if len(profile) != 12 or len(target) != target_n or profile & target:
                raise RuntimeError(f"membership cardinality/disjointness drift: {slug}")
            global_profiles.setdefault(key, set()).update(profile)
            if target_values != a1_targets_by_row.get((slug, key)):
                raise RuntimeError(f"retained target differs from A1: {slug}")
        recomputed_coverage[slug] = _coverage_truth(
            slug=slug,
            targets=group["target_movie_ids"].tolist(),
            users=len(group),
            thresholds=base["coverage_gates"],
        )
    overlap = sum(len(global_profiles[key] & a1_targets[key]) for key in global_profiles)
    if overlap:
        raise RuntimeError(f"global target/profile intersection is nonzero: {overlap}")
    for slug, values in recomputed_coverage.items():
        expected = summary["coverage"][slug]
        for key, value in values.items():
            _compare_number(expected[key], value, f"coverage {slug} {key}")
        if values["coverage_truth"] != "BROAD_ITEM_ELIGIBLE":
            raise RuntimeError(f"coverage gate failed: {slug}")
    random_frame = frame.loc[frame["track"].eq("RANDOM_ITEM_COLD")]
    random_counts = Counter(movie for values in random_frame["target_movie_ids"] for movie in values)
    random_total = sum(random_counts.values())
    pooled = {
        "users": int(random_frame["user_key"].nunique()),
        "target_slots": random_total,
        "unique_target_items": len(random_counts),
        "effective_target_items": random_total * random_total / sum(value * value for value in random_counts.values()),
        "maximum_target_item_load": max(random_counts.values()),
        "top10_target_slot_share": sum(value for _, value in random_counts.most_common(10)) / random_total,
        "coverage_truth": "BROAD_ITEM_ELIGIBLE",
        "gate": "CONJUNCTION_OF_R0_TO_R4",
    }
    for key, expected in pooled.items():
        _compare_number(summary["coverage"]["RANDOM_POOLED"][key], expected, f"coverage RANDOM_POOLED {key}")
    return frame, summary


def create_or_verify_lock(
    amendment_path: Path,
    amendment: dict[str, Any],
    base: dict[str, Any],
    parent: dict[str, Any],
    output_root: Path,
    g: Mapping[str, Path],
    resume: bool,
) -> None:
    frame, summary = validate_membership_and_summary(amendment, base, output_root)
    summary_path = output_root / "membership-preflight.json"
    membership_path = output_root / "cache/membership.parquet"
    implementations = [
        Path(__file__).resolve(),
        ROOT / "scripts/run_rec_ev_028a1.py",
        ROOT / "scripts/train_rec_ev_027_lightfm.py",
        ROOT / "scripts/run_rec_ev_027_screen.py",
        ROOT / "scripts/rec_ev_027_core.py",
        ROOT / "scripts/rec_ev_022a_core.py",
        ROOT / "scripts/preflight_rec_ev_028a2_membership.py",
        ROOT / "scripts/preflight_rec_ev_028a1_membership.py",
        ROOT / "scripts/preflight_rec_ev_028a_membership.py",
        ROOT / "scripts/preflight_rec_ev_027_membership.py",
        ROOT / "scripts/build_rec_ev_019b_features.py",
        ROOT / "scripts/validate_rec_ev_028a2_amendment.py",
        ROOT / "scripts/validate_rec_ev_028a1_amendment.py",
        ROOT / "scripts/validate_rec_ev_028a_contract.py",
    ]
    expected = {
        "schema_version": 1,
        "evidence_id": "REC-EV-028A2-PERSONALIZATION-ATTRIBUTION",
        "status": "LOCKED_BEFORE_ANY_REC_EV_028_RATING_VALUE_ACCESS",
        "amendment": artifact(amendment_path),
        "base_contract": artifact(ROOT / amendment["base_contract"]["path"]),
        "a1_amendment": artifact(ROOT / amendment["a1_amendment"]["path"]),
        "parent_contract": artifact(ROOT / base["inputs"]["parent_contract"]["path"]),
        "membership_preflight": artifact(summary_path),
        "membership": artifact(membership_path),
        "implementations": [artifact(path) for path in implementations],
        "membership_rows": len(frame),
        "canonical_outers": [slug for slug, _, _ in OUTERS],
        "systems": list(SYSTEMS),
        "cells": [cell for cell, _, _ in CELLS],
        "fit_seeds": list(map(int, base["fit"]["seeds"])),
        "rating_values_opened_at_lock": False,
        "timestamps_opened_at_lock": False,
        "future_membership_opened_at_lock": False,
        "locked_test_opened": False,
        "final_reserve_opened": False,
        "product_policy_changed": False,
    }
    if g["lock"].exists():
        if not resume or json.loads(g["lock"].read_text(encoding="utf-8")) != expected:
            raise ResumeError("REC-EV-028A2 protocol lock drift or --resume missing")
    else:
        g["lock"].parent.mkdir(parents=True, exist_ok=True)
        temporary = g["lock"].with_suffix(".tmp")
        temporary.write_text(json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(g["lock"])


def _expected_outer_specs(output_root: Path, artifact_name: str) -> list[str]:
    return [outer_paths(output_root, slug)[artifact_name].resolve().relative_to(ROOT).as_posix() for slug, _, _ in OUTERS]


def verify_prepared(output_root: Path, g: Mapping[str, Path]) -> dict[str, Any]:
    value = json.loads(g["prepared"].read_text(encoding="utf-8"))
    if value.get("status") != "SEALED_ALL_FIT_AND_PROFILES_WITHOUT_TARGET_LABELS":
        raise ResumeError("prepared status drift")
    for key in ("target_rating_values_opened", "timestamps_opened", "future_movie_membership_opened"):
        if value.get(key) is not False:
            raise ResumeError(f"prepared firewall drift: {key}")
    specs = value.get("outer_integrities", [])
    expected_paths = _expected_outer_specs(output_root, "prepared_integrity")
    if len(specs) != len(OUTERS) or [str(spec.get("path")) for spec in specs] != expected_paths:
        raise ResumeError("prepared canonical outer paths drift")
    for (slug, _, _), spec in zip(OUTERS, specs, strict=True):
        body_path = verify_artifact(spec)
        body = json.loads(body_path.read_text(encoding="utf-8"))
        if body.get("status") != "SEALED_FIT_AND_SELECTED_PROFILES_WITHOUT_TARGET_LABELS":
            raise ResumeError(f"prepared outer status drift: {slug}")
        metadata = body.get("metadata", {})
        if metadata.get("outer") != slug or metadata.get("target_rating_values_opened") is not False or metadata.get("timestamps_opened") is not False or metadata.get("future_movie_membership_opened") is not False:
            raise ResumeError(f"prepared outer semantic drift: {slug}")
        p = outer_paths(output_root, slug)
        expected_artifacts = {name: p[name].resolve().relative_to(ROOT).as_posix() for name in ("prior", "interactions", "features", "feature_rows", "profiles")}
        if set(body.get("artifacts", {})) != set(expected_artifacts):
            raise ResumeError(f"prepared artifact set drift: {slug}")
        for name, expected_path in expected_artifacts.items():
            child = body["artifacts"][name]
            if child.get("path") != expected_path:
                raise ResumeError(f"prepared artifact path drift: {slug} {name}")
            verify_artifact(child)
        expected_members = pd.read_parquet(output_root / "cache/membership.parquet")
        expected_members = expected_members.loc[expected_members["outer"].eq(slug)].sort_values(
            "user_key", kind="stable", ignore_index=True
        )
        profiles = pd.read_parquet(p["profiles"]).sort_values("user_key", kind="stable", ignore_index=True)
        if len(profiles) != len(expected_members) or profiles["user_key"].duplicated().any():
            raise ResumeError(f"prepared profile row identity drift: {slug}")
        if profiles["user_key"].astype(str).tolist() != expected_members["user_key"].astype(str).tolist():
            raise ResumeError(f"prepared profile user keys differ from membership: {slug}")
        for column in ("profile_movie_ids", "target_movie_ids"):
            if any(
                _strict_int_list(left, f"prepared {column} {slug}")
                != _strict_int_list(right, f"membership {column} {slug}")
                for left, right in zip(profiles[column], expected_members[column], strict=True)
            ):
                raise ResumeError(f"prepared {column} differs from membership: {slug}")
        if profiles["donor_user_key"].astype(str).tolist() != expected_members["donor_user_key"].astype(str).tolist():
            raise ResumeError(f"prepared donor keys differ from membership: {slug}")
        for key, values in zip(profiles["user_key"], profiles["profile_rating_idx"], strict=True):
            indices = _strict_int_list(values, f"prepared profile ratings {slug} {key}")
            if len(indices) != 12 or any(value < 0 or value > 9 for value in indices):
                raise ResumeError(f"prepared profile rating-index shape drift: {slug}")
        if metadata.get("evaluation_users") != len(expected_members):
            raise ResumeError(f"prepared evaluation-user count drift: {slug}")
        reader = metadata.get("reader", {})
        expected_profile_values = 12 * len(expected_members)
        expected_target_slots = sum(len(values) for values in expected_members["target_movie_ids"])
        if reader.get("evaluation_profile_rating_values_parsed") != expected_profile_values:
            raise ResumeError(f"prepared profile rating count drift: {slug}")
        if reader.get("evaluation_target_rows_seen_without_rating_parse") != expected_target_slots:
            raise ResumeError(f"prepared target membership observation count drift: {slug}")
        if reader.get("evaluation_target_rating_values_parsed") != 0 or reader.get("timestamps_parsed") != 0:
            raise ResumeError(f"prepared target/timestamp parser firewall drift: {slug}")
    return value


def verify_fits(
    base: dict[str, Any], parent: dict[str, Any], output_root: Path, g: Mapping[str, Path]
) -> dict[str, Any]:
    value = json.loads(g["fits"].read_text(encoding="utf-8"))
    seeds = list(map(int, base["fit"]["seeds"]))
    if value.get("status") != "SEALED_ALL_LIGHTFM_FITS" or value.get("seeds") != seeds:
        raise ResumeError("fit global semantic drift")
    specs = value.get("outer_integrities", [])
    expected_paths = _expected_outer_specs(output_root, "fit_integrity")
    if len(specs) != len(OUTERS) or [str(spec.get("path")) for spec in specs] != expected_paths:
        raise ResumeError("fit canonical outer paths drift")
    for (slug, _, _), spec in zip(OUTERS, specs, strict=True):
        body_path = verify_artifact(spec)
        body = json.loads(body_path.read_text(encoding="utf-8"))
        if body.get("status") != "SEALED_LIGHTFM_THREE_SEEDS" or body.get("outer") != slug:
            raise ResumeError(f"fit outer semantic drift: {slug}")
        jobs = body.get("jobs", [])
        expected_job_paths = [
            (outer_paths(output_root, slug)["base"] / f"fits/lightfm/S{seed}/integrity.json").resolve().relative_to(ROOT).as_posix()
            for seed in seeds
        ]
        if len(jobs) != len(seeds) or [str(job.get("path")) for job in jobs] != expected_job_paths:
            raise ResumeError(f"fit seed paths drift: {slug}")
        for seed, job in zip(seeds, jobs, strict=True):
            job_path = verify_artifact(job)
            job_body = json.loads(job_path.read_text(encoding="utf-8"))
            if job_body.get("status") != "SEALED_LIGHTFM_SEED" or job_body.get("outer") != slug or job_body.get("seed") != seed:
                raise ResumeError(f"fit job semantic drift: {slug} S{seed}")
            job_directory = outer_paths(output_root, slug)["base"] / f"fits/lightfm/S{seed}"
            expected_config_path = (job_directory / "config.json").resolve()
            expected_result_path = (job_directory / "result.npz").resolve()
            config_path = verify_artifact(job_body["config"])
            result_path = verify_artifact(job_body["result"])
            if config_path.resolve() != expected_config_path or result_path.resolve() != expected_result_path:
                raise ResumeError(f"fit job child path drift: {slug} S{seed}")
            parameters = parent["models"]["FEATURE_ONLY_LIGHTFM"]["parameters"]
            p = outer_paths(output_root, slug)
            expected_config = {
                "seed": seed,
                "dimension": int(parameters["dimension"]),
                "epochs": int(parameters["epochs"]),
                "learning_rate": float(parameters["learning_rate"]),
                "item_alpha": float(parameters["item_alpha"]),
                "user_alpha": float(parameters["user_alpha"]),
                "interaction_sha256": sha256_file(p["interactions"]),
                "item_feature_sha256": sha256_file(p["features"]),
                "item_identity_features": False,
            }
            if json.loads(config_path.read_text(encoding="utf-8")) != expected_config:
                raise ResumeError(f"fit job config content drift: {slug} S{seed}")
            fitted = np.load(result_path, allow_pickle=False)
            if fitted["item_factors"].shape != (85_517, 128) or fitted["item_biases"].shape != (85_517,) or not np.isfinite(fitted["item_factors"]).all() or not np.isfinite(fitted["item_biases"]).all():
                raise ResumeError(f"fit array drift: {slug} S{seed}")
    return value


def verify_scores(output_root: Path, g: Mapping[str, Path]) -> dict[str, Any]:
    value = json.loads(g["scores"].read_text(encoding="utf-8"))
    required_false = ("target_rating_values_opened", "timestamps_opened", "future_movie_membership_opened", "locked_test_opened", "final_reserve_opened", "product_policy_changed")
    if value.get("status") != "GLOBAL_PRELABEL_SCORE_SEAL_COMPLETE" or value.get("systems") != list(SYSTEMS) or value.get("cells") != [cell for cell, _, _ in CELLS] or any(value.get(key) is not False for key in required_false):
        raise ResumeError("global score seal semantic drift")
    specs = value.get("outer_integrities", [])
    expected_paths = _expected_outer_specs(output_root, "score_integrity")
    if len(specs) != len(OUTERS) or [str(spec.get("path")) for spec in specs] != expected_paths:
        raise ResumeError("score canonical outer paths drift")
    total_rows = 0
    for (slug, _, _), spec in zip(OUTERS, specs, strict=True):
        body_path = verify_artifact(spec)
        body = json.loads(body_path.read_text(encoding="utf-8"))
        if body.get("status") != "SEALED_ALL_NONRANDOM_SYSTEMS_AND_CELLS_BEFORE_TARGET_LABELS" or body.get("outer") != slug or body.get("target_rating_values_opened") is not False or body.get("timestamps_opened") is not False or body.get("future_movie_membership_opened") is not False:
            raise ResumeError(f"score outer semantic drift: {slug}")
        score_path = verify_artifact(body["score_ranks"])
        if score_path.resolve() != outer_paths(output_root, slug)["scores"].resolve():
            raise ResumeError(f"score path drift: {slug}")
        scores = pd.read_parquet(score_path)
        profiles = pd.read_parquet(
            outer_paths(output_root, slug)["profiles"], columns=["user_key", "target_movie_ids"]
        )
        profile_targets = {
            str(row.user_key): _strict_int_list(row.target_movie_ids, f"score profile targets {slug} {row.user_key}")
            for row in profiles.itertuples(index=False)
        }
        users = len(profile_targets)
        expected_rows = users * len(SYSTEMS) * len(CELLS)
        if len(scores) != expected_rows or body.get("rows") != expected_rows or body.get("users") != users or scores.duplicated(["user_key", "cell", "system"]).any():
            raise ResumeError(f"score row identity drift: {slug}")
        if set(scores["system"]) != set(SYSTEMS) or set(scores["cell"]) != {cell for cell, _, _ in CELLS}:
            raise ResumeError(f"score Cartesian values drift: {slug}")
        if scores["active"].isna().any() or not pd.api.types.is_bool_dtype(scores["active"].dtype):
            raise ResumeError(f"score active dtype drift: {slug}")
        if set(scores["user_key"].astype(str)) != set(profile_targets):
            raise ResumeError(f"score user keys differ from prepared profiles: {slug}")
        expected_pairs = {(cell, system) for cell, _, _ in CELLS for system in SYSTEMS}
        expected_track, expected_fold = next((track, fold) for outer, track, fold in OUTERS if outer == slug)
        for key, group in scores.groupby("user_key", sort=False):
            pairs = set(zip(group["cell"].astype(str), group["system"].astype(str), strict=True))
            if len(group) != len(expected_pairs) or pairs != expected_pairs:
                raise ResumeError(f"per-user score Cartesian drift: {slug} {key}")
            expected_targets = profile_targets[str(key)]
            if any(
                _strict_int_list(values, f"score targets {slug} {key}") != expected_targets
                for values in group["target_movie_ids"]
            ):
                raise ResumeError(f"score targets differ from prepared profiles: {slug} {key}")
            target_n = len(expected_targets)
            for row in group.itertuples(index=False):
                if str(row.outer) != slug or str(row.track) != expected_track or str(row.fold_or_domain) != expected_fold:
                    raise ResumeError(f"score outer metadata drift: {slug} {key}")
                expected_encoding, expected_k = next(
                    (encoding, k) for cell, encoding, k in CELLS if cell == str(row.cell)
                )
                if str(row.encoding) != expected_encoding or int(row.k) != expected_k:
                    raise ResumeError(f"score cell metadata drift: {slug} {key} {row.cell}")
                if not isinstance(row.active, (bool, np.bool_)):
                    raise ResumeError(f"score active scalar type drift: {slug} {key}")
                if row.active:
                    ranks = _strict_int_list(row.ranked_target_indices, f"score ranks {slug} {key}")
                    values = _strict_float_list(row.scores, f"scores {slug} {key}")
                    if len(ranks) != target_n or sorted(ranks) != list(range(target_n)) or len(values) != target_n or not np.isfinite(values).all() or np.unique(values).size < 2:
                        raise ResumeError(f"active score payload drift: {slug} {key} {row.cell} {row.system}")
                    recomputed = deterministic_order(
                        np.asarray(expected_targets, dtype=np.int64),
                        np.asarray(values, dtype=np.float64),
                        phase="ATTRIBUTION",
                        track=expected_track,
                        fold=expected_fold,
                        model=str(row.system),
                        encoding=str(row.cell),
                        user_key=str(key),
                    ).astype(int).tolist()
                    if ranks != recomputed:
                        raise ResumeError(f"score/rank deterministic-order drift: {slug} {key} {row.cell} {row.system}")
                elif len(row.ranked_target_indices) or len(row.scores):
                    raise ResumeError(f"inactive score payload drift: {slug} {key} {row.cell} {row.system}")
        total_rows += expected_rows
    if value.get("rows") != total_rows:
        raise ResumeError("global score row count drift")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--amendment", type=Path, default=AMENDMENT_DEFAULT)
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/recommendation-evidence/rec-ev-028a2")
    parser.add_argument("--phase", choices=("prepare", "fit", "score", "all"), default="all")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-jobs", type=int, default=3)
    args = parser.parse_args()
    if args.max_jobs < 1:
        raise ValueError("--max-jobs must be positive")
    amendment_path = args.amendment.resolve()
    output_root = args.output_root.resolve()
    amendment, base, _, parent = load_contracts(amendment_path)
    g = global_paths(output_root)
    create_or_verify_lock(amendment_path, amendment, base, parent, output_root, g, args.resume)
    phases = ("prepare", "fit", "score") if args.phase == "all" else (args.phase,)
    result: Any = None
    for phase in phases:
        if phase == "prepare":
            validate_membership_and_summary(amendment, base, output_root)
            if g["prepared"].exists():
                if not args.resume:
                    raise ResumeError("prepared state requires --resume")
                result = verify_prepared(output_root, g)
            else:
                result = prepare_all(base, parent, output_root, g, args.resume)
                verify_prepared(output_root, g)
        elif phase == "fit":
            verify_prepared(output_root, g)
            if g["fits"].exists():
                if not args.resume:
                    raise ResumeError("fit state requires --resume")
                result = verify_fits(base, parent, output_root, g)
            else:
                result = fit_all(base, parent, output_root, g, args.resume, args.max_jobs)
                verify_fits(base, parent, output_root, g)
        elif phase == "score":
            verify_prepared(output_root, g)
            verify_fits(base, parent, output_root, g)
            if g["scores"].exists():
                if not args.resume:
                    raise ResumeError("score state requires --resume")
                result = verify_scores(output_root, g)
            else:
                result = score_all(base, parent, output_root, g, args.resume)
                verify_scores(output_root, g)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
