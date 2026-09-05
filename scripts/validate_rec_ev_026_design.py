"""Fail closed on the exact REC-EV-026 adaptive mechanism-screen design."""

from __future__ import annotations

import argparse
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-026-content-cf-alignment-design.json"
EXPECTED_CANONICAL_SHA256 = "0035d8e2a384491be73f6ccff2cda71355daa8c8be287aef2f6380688db10cd9"
EXPECTED_TOP_KEYS = [
    "schema_version", "contract_id", "status", "purpose", "design_audit", "research_basis",
    "motivation_only", "authorization", "source_users", "allowed_input_artifacts", "teacher",
    "forbidden_input_artifacts", "exposure_registry", "common_support", "experiments", "membership",
    "mapper", "cells", "heads", "scoring", "rating_scale", "metrics", "statistics", "decision",
    "phase_order", "output_root", "outputs", "resume", "claim_boundary", "stop_rule",
]
EXPECTED_ALLOWED_INPUTS = {
    "movielens_archive", "structured_features", "text_embeddings", "korean_projection", "train_prior",
    "candidate_core", "teacher_contract", "teacher_trial_registry", "teacher_selection_lock",
}
EXPECTED_REGISTRY_IDS = [
    "GLOBAL_VALIDATION_ALL_PAIRS_SUPERSET_020P", "019A_ROUTER_WINDOWS", "019A_VALIDATION_WINDOWS",
    "019F_FRESH_WINDOWS", "019F_INVALID_FLOAT64_FRESH_WINDOWS", "022A_TARGET", "022A_INVALID_TARGET",
    "022B_TARGET", "022B_INVALID_TARGET", "023B_TARGET_AND_KOREAN_COLD", "023E_TARGET_CONTROL",
    "023F_TARGET_CONTROL", "024A_TARGET_CONTROL", "024B_TARGET_CONTROL", "025A_R1_TARGET_CONTROL",
    "025B_R1_TARGET_CONTROL",
]
EXPECTED_REGISTRY_NAMESPACE = {
    "GLOBAL_VALIDATION_ALL_PAIRS_SUPERSET_020P": "RAW_TO_BOTH",
    "019A_ROUTER_WINDOWS": "019", "019A_VALIDATION_WINDOWS": "019",
    "019F_FRESH_WINDOWS": "019", "019F_INVALID_FLOAT64_FRESH_WINDOWS": "019",
    "022A_TARGET": "022", "022A_INVALID_TARGET": "022", "022B_TARGET": "022",
    "022B_INVALID_TARGET": "022", "023B_TARGET_AND_KOREAN_COLD": "022",
    "023E_TARGET_CONTROL": "022", "023F_TARGET_CONTROL": "022", "024A_TARGET_CONTROL": "022",
    "024B_TARGET_CONTROL": "022", "025A_R1_TARGET_CONTROL": "022", "025B_R1_TARGET_CONTROL": "022",
}
EXPECTED_CELLS = [
    {"encoding": "BINARY_SIGN", "k": 6}, {"encoding": "PERCENTILE_MAGNITUDE", "k": 6},
    {"encoding": "BINARY_SIGN", "k": 8}, {"encoding": "PERCENTILE_MAGNITUDE", "k": 8},
    {"encoding": "BINARY_SIGN", "k": 14}, {"encoding": "PERCENTILE_MAGNITUDE", "k": 14},
]
EXPECTED_STATUS_PRECEDENCE = [
    "INFEASIBLE_PRELABEL", "INFEASIBLE_MAPPER_FIT_PRELABEL",
    "INCONCLUSIVE_PRECISION_OR_NONESTIMABLE", "ROBUST_ALIGNMENT_SIGNAL",
    "DOMAIN_SPECIFIC_ALIGNMENT_SIGNAL_NOT_GLOBAL", "CELL_SPECIFIC_SIGNAL_NOT_ROBUST",
    "NO_ROBUST_ALIGNMENT_SIGNAL",
]


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def verify_artifact(spec: dict[str, Any]) -> None:
    require(set(spec) >= {"path", "bytes", "sha256"}, "artifact pin incomplete")
    path = resolve(str(spec["path"]))
    require(path.is_file(), f"missing artifact: {path}")
    require(path.stat().st_size == int(spec["bytes"]), f"byte drift: {path}")
    require(sha256_file(path) == spec["sha256"], f"hash drift: {path}")


def dotted_get(value: dict[str, Any], dotted: str) -> Any:
    current: Any = value
    for key in dotted.split("."):
        require(isinstance(current, dict) and key in current, f"missing proof field: {dotted}")
        current = current[key]
    return current


def verify_json_expectations(spec: dict[str, Any]) -> dict[str, Any]:
    verify_artifact(spec)
    value = load(resolve(spec["path"]))
    for dotted, expected in spec.get("expected", {}).items():
        require(dotted_get(value, dotted) == expected, f"reuse/no-outcome proof drift: {dotted}")
    return value


def verify_failed_root(proof: dict[str, Any], key: str) -> None:
    require(set(proof) == {"files", "forbidden_additional_files"}, f"{key} proof shape drift")
    require(proof["forbidden_additional_files"] is True and len(proof["files"]) == 2, f"{key} guard drift")
    for spec in proof["files"]:
        verify_artifact(spec)
    roots = {resolve(item["path"]).parent for item in proof["files"]}
    require(len(roots) == 1, f"{key} root drift")
    root = next(iter(roots))
    actual = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
    require(actual == ["protocol-lock.json", "source-manifest.json"], f"{key} contains outcome artifacts")


def contrast_metadata(contract: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    metrics = contract["metrics"]
    for experiment in ("REC-EV-026A", "REC-EV-026B"):
        for head in contract["heads"]["reporting_order"]:
            for cell in contract["cells"]:
                for contrast_class in metrics["absolute_classes"]:
                    for endpoint in metrics["absolute_endpoints"]:
                        rows.append({"index": len(rows), "experiment": experiment, "kind": "ABSOLUTE", "head": head, "encoding": cell["encoding"], "k": cell["k"], "class": contrast_class, "endpoint": endpoint})
        for baseline in metrics["incremental_baselines"]:
            for cell in contract["cells"]:
                for domain in metrics["incremental_domains"]:
                    for endpoint in metrics["incremental_endpoints"]:
                        rows.append({"index": len(rows), "experiment": experiment, "kind": "INCREMENTAL", "baseline": baseline, "encoding": cell["encoding"], "k": cell["k"], "domain": domain, "endpoint": endpoint})
    return rows


def bootstrap_uint64(attempt: int, user_key022: str) -> int:
    payload = ("feelm-bootstrap-v1|rec-ev-026-content-cf-alignment-v1|" f"{attempt}|user|{user_key022}").encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)


def bootstrap_weight(attempt: int, user_key022: str) -> int:
    integer = bootstrap_uint64(attempt, user_key022)
    with localcontext() as context:
        context.prec = 80
        uniform = Decimal(integer) / Decimal(2**64)
        term = (-Decimal(1)).exp()
        cdf = term
        k = 0
        while uniform > cdf:
            k += 1
            term /= Decimal(k)
            cdf += term
        return k


def verify_teacher(contract: dict[str, Any]) -> None:
    teacher = contract["teacher"]
    require(teacher["model"] == "B4_BPR_MF-T003", "teacher drift")
    require(teacher["parameters"] == {"factors": 128, "regularization": 0.0001}, "teacher params drift")
    require(teacher["seeds"] == [17, 42, 73, 101, 211], "teacher seeds drift")
    require(teacher["selection_basis"] == "REC_EV_019C_K10_SELECTED_ONLY; K5_SELECTED_T002", "teacher selection basis drift")
    require(teacher["rec_ev_026_reselection"] is False, "teacher reselection enabled")
    require(list(teacher["factor_artifacts"]) == ["17", "42", "73", "101", "211"], "factor order drift")
    core_spec = contract["allowed_input_artifacts"]["candidate_core"]
    movie_ids = pd.read_parquet(resolve(core_spec["path"]), columns=["movie_id"])["movie_id"].to_numpy()
    require(movie_ids.shape == (41625,), "candidate core row count drift")
    require(np.issubdtype(movie_ids.dtype, np.integer), "candidate core movie_id dtype drift")
    require(bool(np.all(movie_ids[1:] > movie_ids[:-1])), "candidate core movie_id order/uniqueness drift")
    for seed, spec in teacher["factor_artifacts"].items():
        verify_artifact(spec)
        with np.load(resolve(spec["path"]), allow_pickle=False) as archive:
            require(archive.files == ["item_factors"], f"seed {seed} NPZ key drift")
            factors = archive["item_factors"]
            require(factors.shape == (41625, 128), f"seed {seed} factor shape drift")
            require(factors.dtype == np.dtype("float32"), f"seed {seed} factor dtype drift")
            require(bool(np.isfinite(factors).all()), f"seed {seed} nonfinite factors")
    teacher_contract = load(resolve(contract["allowed_input_artifacts"]["teacher_contract"]["path"]))
    require(teacher_contract["base_training_semantics"]["training_users"] == "BASE_TRAIN_ONLY", "teacher train role drift")
    registry = load(resolve(contract["allowed_input_artifacts"]["teacher_trial_registry"]["path"]))
    trial = next(row for row in registry["trials"]["B4_BPR_MF"] if row["trial_id"] == "B4_BPR_MF-T003")
    require(trial["parameters"] == {"factors": 128, "regularization": 0.0001}, "T003 registry drift")
    require(trial["seeds"] == [17, 42, 73, 101, 211], "T003 seed registry drift")
    selection = load(resolve(contract["allowed_input_artifacts"]["teacher_selection_lock"]["path"]))
    require(selection["selected_trial_ids"]["B4_BPR_MF"]["10"] == "B4_BPR_MF-T003", "K10 teacher selection drift")
    require(selection["selected_trial_ids"]["B4_BPR_MF"]["5"] == "B4_BPR_MF-T002", "K5 teacher selection drift")


def verify_reuse_and_no_outcome(contract: dict[str, Any]) -> None:
    proofs = contract["exposure_registry"]["reuse_proofs"]
    require(set(proofs) == {"023A", "023C", "023D", "023D_A1", "019C_019D_019E", "020P"}, "reuse proof inventory drift")
    for key in ("023A", "023C", "023D", "023D_A1"):
        verify_json_expectations(proofs[key])
    chain = proofs["019C_019D_019E"]
    require(set(chain) == {"files", "expected"} and len(chain["files"]) == 3, "019 chain proof shape drift")
    for spec in chain["files"]:
        verify_artifact(spec)
    c19, d19, e19 = [load(resolve(spec["path"])) for spec in chain["files"]]
    shared = chain["expected"]["shared_validation_windows_path"]
    require(c19["allowed_input_artifacts"]["validation_windows"] == shared, "019C membership source drift")
    require(d19["allowed_input_artifacts"]["validation_windows"]["path"] == shared, "019D membership source drift")
    require(e19["allowed_input_artifacts"]["validation_windows"]["path"] == shared, "019E membership source drift")
    require(e19["source_ranking_reuse"]["allowed"] is True and e19["source_ranking_reuse"]["no_new_model_fit_or_scoring"] is True, "019E reuse drift")
    p20 = proofs["020P"]
    require(set(p20) == {"files", "expected"} and len(p20["files"]) == 2, "020P proof shape drift")
    for spec in p20["files"]:
        verify_artifact(spec)
    p20_contract = load(resolve(p20["files"][0]["path"]))
    require(p20_contract["inputs"]["role"] == "VALIDATION_ONLY" and p20_contract["gates"]["locked_test_opened"] is False, "020P role/firewall drift")
    source = resolve(p20["files"][1]["path"]).read_text(encoding="utf-8")
    require('movie_ids = frame["movieId"].astype(int).tolist()' in source, "020P movie universe drift")
    require("ordered = locked_movie_order(seed, raw_user_id, movie_ids)" in source and "slate = ordered[:size]" in source, "020P slate inclusion proof drift")
    require(p20["expected"]["global_validation_all_pairs_is_superset"] is True, "020P registry coverage drift")
    no_outcome = contract["exposure_registry"]["no_outcome_proofs"]
    require(set(no_outcome) == {"021P", "021V", "025A_FAILED", "025B_FAILED"}, "no-outcome proof inventory drift")
    verify_json_expectations(no_outcome["021P"])
    verify_json_expectations(no_outcome["021V"])
    for key in ("025A_FAILED", "025B_FAILED"):
        verify_failed_root(no_outcome[key], key)


def validate(contract: dict[str, Any]) -> None:
    require(list(contract) == EXPECTED_TOP_KEYS, "top-level key set/order drift")
    require(contract["contract_id"] == "rec-ev-026-content-cf-alignment-design-v1", "contract id drift")
    require(contract["status"] == "PROPOSED_FOR_INDEPENDENT_DESIGN_AUDIT", "status drift")
    require(set(contract["allowed_input_artifacts"]) == EXPECTED_ALLOWED_INPUTS, "allowed input inventory drift")
    for spec in contract["allowed_input_artifacts"].values():
        verify_artifact(spec)
    auth = contract["authorization"]
    require(auth == {"adaptive_development": True, "id_only_preflight": True, "feature_and_frozen_teacher_read": True, "profile_rating_read_before_mapper_fit_gate": False, "evaluation_rating_read_before_all_head_rank_seal": False, "timestamp_read": False, "locked_test_access": False, "final_reserve_access": False, "champion_selection": False, "product_policy_change": False}, "authorization drift")
    users = contract["source_users"]
    require(users["old_user_bucket_inclusive"] == [40, 59], "teacher/evaluation user split drift")
    require(users["teacher_training_old_user_bucket_inclusive"] == [0, 39], "teacher split drift")
    require(users["user_role_ranges_inclusive"] == [[6000, 7999], [8000, 9199]], "development role drift")
    require(users["teacher_evaluation_user_intersection"] == 0, "teacher user overlap allowed")
    require(users["raw_user_id_output"] == "FORBIDDEN", "raw user output enabled")
    require(contract["forbidden_input_artifacts"] == ["outputs/recommendation-evidence/global-time-v1/test.parquet", "outputs/recommendation-evidence/rec-ev-019a/evaluation-windows.parquet", "outputs/recommendation-evidence/rec-ev-019a/locked-test-binary-prefixes.parquet", "outputs/recommendation-evidence/rec-ev-019a/locked-test-evaluation-windows.parquet", "outputs/recommendation-evidence/rec-ev-022a/final-reserve-input.parquet"], "forbidden artifact inventory drift")
    verify_teacher(contract)
    registry = contract["exposure_registry"]
    require(registry["freshness_claim"] == "USER_MOVIE_PAIR_NONREUSE_SINCE_REC_EV_019A_ONLY", "freshness overclaim")
    require(registry["pre_019a_exposure"] == "NOT_RECONSTRUCTED_DUE_LOCKED_SOURCE", "pre-019A exposure overclaim")
    require(registry["namespace_019"] == "LOWERHEX_SHA256_UTF8_FEELM_ML32M_USER_V1_PIPE_DECIMAL_USER_ID", "019 namespace drift")
    require(registry["namespace_022"] == "LOWERHEX_SHA256_UTF8_REC_EV_022A_USER_KEY_V1_PIPE_DECIMAL_USER_ID", "022 namespace drift")
    require(registry["raw_global_validation_reader_order"] == ["PARSE_USER_ID", "REQUIRE_OLD_BUCKET_40_59", "REQUIRE_ROLE_STAGE1_SELECTION_OR_STAGE2_DEVELOPMENT", "ONLY_THEN_READ_MOVIE_ID", "COMPUTE_KEY019_AND_KEY022", "DISCARD_RAW_USER_ID"], "raw registry reader order drift")
    require(registry["rating_q_metric_timestamp_columns_read"] is False, "registry can read outcomes")
    require(registry["read_columns_only"] == ["user_id_or_user_key", "movie_id_or_movie_id_lists"], "registry read surface drift")
    require([row["id"] for row in registry["sources"]] == EXPECTED_REGISTRY_IDS, "registry source inventory/order drift")
    for spec in registry["sources"]:
        require(set(spec) == {"id", "path", "bytes", "sha256", "namespace", "columns"}, "registry source key drift")
        verify_artifact(spec)
        require(spec["namespace"] == EXPECTED_REGISTRY_NAMESPACE[spec["id"]], "registry namespace drift")
        lowered = [str(column).lower() for column in spec["columns"]]
        require(not any(any(token in column for token in ("rating", "timestamp", "metric", "q_eval", "target_rating_idx", "control_rating_idx")) for column in lowered), "registry outcome column allowed")
    verify_reuse_and_no_outcome(contract)
    require(contract["common_support"] == {"structured": "FEATURE_ELIGIBLE_TRUE_AND_RELEASE_YEAR_NONNULL_AND_CURRENT_FULL_NONZERO", "e5_model_id": "intfloat/multilingual-e5-small", "e5_revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3", "e5_dimension": 384, "e5": "FEATURE_ELIGIBLE_TRUE_AND_REVISION_EXACT_AND_ALL_FINITE_AND_ABS_L2_MINUS_1_LTE_0_0001", "intersection": "STRUCTURED_INTERSECT_E5_BY_UNIQUE_MOVIE_ID", "expected_items": 68078}, "common support drift")
    experiments = contract["experiments"]
    require(list(experiments) == ["REC-EV-026A", "REC-EV-026B"], "experiment order drift")
    expected_domains = {"REC-EV-026A": ("NON_KOREAN_COMMON_SUPPORT", "KOREAN_ORIGIN_COMMON_SUPPORT", 150, 60, 60, "rec-ev-026a"), "REC-EV-026B": ("PRE2020_COMMON_SUPPORT", "RELEASE_2020_2023_COMMON_SUPPORT", 400, 200, 200, "rec-ev-026b")}
    for evidence_id, spec in experiments.items():
        source, target, users_floor, target_floor, control_floor, prefix = expected_domains[evidence_id]
        require(spec["source_domain"] == source and spec["target_domain"] == target, f"{evidence_id} domain drift")
        require((spec["minimum_users"], spec["minimum_unique_targets"], spec["minimum_unique_controls"]) == (users_floor, target_floor, control_floor), f"{evidence_id} floor drift")
        require((spec["profile_n"], spec["target_n"], spec["control_n"], spec["panels"]) == (14, 4, 4, 4), f"{evidence_id} panel drift")
        salts = [spec["profile_salt"], spec["mapper_split_salt"], *spec["target_salts"], *spec["control_salts"]]
        require(len(salts) == len(set(salts)), f"{evidence_id} duplicate salt")
        require(spec["profile_salt"] == f"{prefix}-source-profile-v1", f"{evidence_id} profile salt drift")
        require(spec["target_salts"] == [f"{prefix}-panel-{i}-fresh-target-v1" for i in range(4)], f"{evidence_id} target salt drift")
        require(spec["control_salts"] == [f"{prefix}-panel-{i}-fresh-control-v1" for i in range(4)], f"{evidence_id} control salt drift")
        require(spec["mapper_split_salt"] == f"{prefix}-mapper-split-v1", f"{evidence_id} mapper salt drift")
    membership = contract["membership"]
    require(membership["selection"] == "SHA256_UTF8_SALT_PIPE_USER_KEY022_PIPE_ROLE_PIPE_MOVIE_ID_RAW_DIGEST_ASC_THEN_MOVIE_ID_ASC", "membership selection drift")
    require(membership["selection_forbidden"] == ["RATING", "Q", "TIMESTAMP", "POPULARITY", "MODEL_SCORE"], "membership leakage ban drift")
    require(membership["profile"] == "FIRST14_SOURCE_COMMON_SUPPORT_AND_T003_FACTOR_SUPPORT", "profile membership drift")
    require(membership["target"] == "FIRST4_FRESH_TARGET_DOMAIN_PER_PANEL" and membership["control"] == "FIRST4_FRESH_SOURCE_DOMAIN_MINUS_PROFILE_PER_PANEL", "slate membership drift")
    require(membership["within_panel_unique"] is True and membership["profile_control_disjoint"] is True, "membership disjointness drift")
    require(membership["bootstrap_unit"] == "USER_ONLY", "bootstrap unit drift")
    mapper = contract["mapper"]
    require(mapper["mapper_items"] == "COMMON_SUPPORT_INTERSECT_T003_FACTOR_SUPPORT_INTERSECT_SOURCE_DOMAIN_MINUS_ENTIRE_TARGET_DOMAIN_MINUS_GLOBAL_SELECTED_CONTROL_UNION", "mapper item set drift")
    require(mapper["split"] == "UINT64_BE_FIRST8_SHA256_UTF8_EXPERIMENT_MAPPER_SPLIT_SALT_PIPE_MOVIE_ID_MOD_100_LT_80_TRAIN_ELSE_VALIDATION_SHARED_ALL_SEEDS", "mapper split drift")
    require(mapper["x"] == "PINNED_ROW_L2_NORMALIZED_E5_FLOAT64" and mapper["y"] == "PINNED_T003_ITEM_FACTOR_ROW_L2_NORMALIZED_FLOAT64", "mapper matrix drift")
    require(mapper["ridge"] == "NO_INTERCEPT_NO_CENTERING_B_EQUALS_SOLVE_XTX_PLUS_ALPHA_I_COMMA_XTY_FLOAT64", "ridge drift")
    require(mapper["alpha_grid"] == [0.1, 1.0, 10.0, 100.0, 1000.0], "alpha grid drift")
    require(mapper["alpha_selection"] == "ONE_ALPHA_PER_EXPERIMENT_EQUAL_MEAN_VALIDATION_COSINE_OVER_ALL_FIVE_SEEDS_AND_ALL_VALIDATION_ITEMS_SMALLEST_ALPHA_AMONG_EXACT_MAXIMIZERS", "alpha selection drift")
    require(mapper["refit"] == "AFTER_ALPHA_SELECTION_REFIT_EACH_SEED_ON_TRAIN_UNION_VALIDATION", "mapper refit drift")
    require((mapper["minimum_train_items"], mapper["minimum_validation_items"]) == (1000, 200), "mapper floors drift")
    require(mapper["fit_gate"] == ["EACH_SEED_VALIDATION_MEAN_COSINE_STRICTLY_POSITIVE", "ALL_REFIT_COEFFICIENTS_FINITE", "ALL_EVALUATION_PREDICTIONS_FINITE_AND_NONZERO"], "mapper fit gate drift")
    require(mapper["candidate_factor_access"].startswith("TARGET_AND_CONTROL_ACTUAL_FACTOR_ROWS_FORBIDDEN"), "candidate factor leakage")
    require(mapper["profile_factor_access"] == "ACTUAL_NORMALIZED_T003_FACTOR_ROWS_ALLOWED_FOR_SELECTED_SOURCE_PROFILE_ONLY", "profile factor access drift")
    require(mapper["seed_outcome_stability"] == "DESCRIPTIVE_ONLY_NOT_GATE_NOT_SELECTION", "seed outcome gate drift")
    require(contract["cells"] == EXPECTED_CELLS, "cell family drift")
    heads = contract["heads"]
    require(heads["reporting_order"] == ["CURRENT_FULL", "E5", "E5_TO_BPR"], "head order drift")
    tie = "UTF8(rec-ev-026-rank-tie-v1|EVIDENCE_ID|USER_KEY022|PANEL|DOMAIN|ENCODING|K|MOVIE_ID)"
    require(heads["tie_payload"] == tie and "HEAD" not in heads["tie_payload"] and "SEED" not in heads["tie_payload"], "tie payload drift")
    require(contract["scoring"] == {"profile_prefix": "FIXED_PROFILE14_FIRST_K_PREFIX_SHARED_BY_ALL_HEADS", "CURRENT_FULL": "L2_NORMALIZE(SUM(weight_i*PINNED_NORMALIZED_CURRENT_FULL_i)); candidate=PINNED_NORMALIZED_CURRENT_FULL; score=COSINE_WITH_NEGATIVE_PRESERVED", "E5": "L2_NORMALIZE(SUM(weight_i*PINNED_NORMALIZED_E5_i)); candidate=PINNED_NORMALIZED_E5; score=COSINE_WITH_NEGATIVE_PRESERVED", "E5_TO_BPR_profile_per_seed": "L2_NORMALIZE(SUM(weight_i*PINNED_NORMALIZED_ACTUAL_T003_FACTOR_i))", "E5_TO_BPR_candidate_per_seed": "L2_NORMALIZE(PINNED_NORMALIZED_E5_TIMES_REFIT_MAPPER_SEED)", "E5_TO_BPR_score": "ARITHMETIC_MEAN_OF_EXACTLY_FIVE_SEED_COSINES", "invalid_seed_rule": "ANY_ONE_SEED_PROFILE_OR_CANDIDATE_INVALID_MAKES_WHOLE_E5_TO_BPR_ROW_INACTIVE", "surviving_seed_average": "FORBIDDEN", "inactive_fallback": "EXACT_ANALYTIC_RANDOM_TOP2"}, "scoring drift")
    require(contract["rating_scale"] == {"prior_file_key": "g0_mid", "tau": 5.0, "profile_q": "N_LT_PLUS_0_5_N_EQ_PLUS_5_G0_MID_DIV_K_PLUS_5", "BINARY_SIGN": "SIGN_2Q_MINUS_1_ZERO_NEUTRAL", "PERCENTILE_MAGNITUDE": "2Q_MINUS_1", "evaluation_q": "FULL_ALLOWED_USER_HISTORY_MID_PERCENTILE_EVALUATION_ONLY"}, "rating scale/timestamp drift")
    metrics = contract["metrics"]
    require((metrics["slate_n"], metrics["top_n"]) == (4, 2), "Top-2 slate drift")
    require(metrics["utility"] == "MEAN_TOP2_FULL_USER_MID_PERCENTILE_Q", "utility sign drift")
    require(metrics["loss"] == "ONE_MINUS_MIN_TOP2_Q", "loss sign drift")
    require(metrics["absolute_endpoints"] == ["UTILITY_IMPROVEMENT_MODEL_MINUS_RANDOM", "SAFETY_IMPROVEMENT_RANDOM_LOSS_MINUS_MODEL"], "absolute endpoint sign drift")
    require(metrics["incremental_endpoints"] == ["UTILITY_E5_TO_BPR_MINUS_BASELINE", "SAFETY_BASELINE_LOSS_MINUS_E5_TO_BPR_LOSS"], "incremental endpoint sign drift")
    stats = contract["statistics"]
    require(stats["primary_unit"] == "USER_AFTER_ARITHMETIC_MEAN_OF_FOUR_PANELS" and stats["joint_family"] == "2_EXPERIMENTS_X_156_EQUALS_312", "statistics family/unit drift")
    rows = contrast_metadata(contract)
    require(len(rows) == 312 and [row["index"] for row in rows] == list(range(312)), "contrast enumeration drift")
    enumeration = stats["contrast_enumeration"]
    require(enumeration["indices"] == [0, 311] and enumeration["count"] == 312, "contrast count/index drift")
    require(enumeration["canonical_metadata_sha256"] == canonical_sha256(rows), "contrast metadata hash drift")
    require(stats["bootstrap_namespace"] == "feelm-bootstrap-v1|rec-ev-026-content-cf-alignment-v1|ATTEMPT|user|USER_KEY022", "bootstrap namespace drift")
    require(stats["bootstrap_transform"] == "SHA256_UTF8_PAYLOAD_FIRST_UINT64_BE; U=DECIMAL80(UINT64/2^64); WEIGHT=MIN_K_WITH_POISSON1_CDF_GTE_U", "bootstrap transform drift")
    for fixture in stats["bootstrap_golden_fixtures"]:
        require(bootstrap_uint64(fixture["attempt"], fixture["user_key022"]) == fixture["uint64"], "bootstrap uint64 fixture drift")
        require(bootstrap_weight(fixture["attempt"], fixture["user_key022"]) == fixture["weight"], "bootstrap weight fixture drift")
    require(stats["shared_union_user_weight"] is True and stats["attempts"] == [0, 7999] and stats["valid_replicates"] == 4000, "bootstrap replicate drift")
    require(stats["valid_replicate_rule"] == "ALL_312_DENOMINATORS_POSITIVE_AND_ESTIMATES_FINITE", "bootstrap validity drift")
    require(stats["se"] == "STD_4000_REPLICATES_DDOF1" and stats["interval"] == "TWO_SIDED_ABSOLUTE_STUDENTIZED_MAX_T_312_NEAREST_RANK_0_975", "bootstrap interval drift")
    require(stats["all_312_estimable_finite_se_positive_and_precise_required"] is True, "precision gate drift")
    decision = contract["decision"]
    require((decision["maximum_half_width_strict"], decision["absolute_target_margin"], decision["absolute_gap_noninferiority"]) == (0.05, 0.02, -0.02), "absolute decision margin drift")
    require((decision["incremental_target_utility_margin"], decision["incremental_target_safety_margin"], decision["incremental_control_noninferiority"]) == (0.005, 0.01, -0.02), "incremental decision margin drift")
    require(decision["status_precedence"] == EXPECTED_STATUS_PRECEDENCE, "status precedence drift")
    require(decision["result_driven_relaxation"] is False and decision["champion"] is None, "decision boundary drift")
    require(contract["phase_order"] == ["DESIGN_AUDIT", "DESIGN_LOCK", "PREFLIGHT_IMPLEMENTATION_AUDIT", "ID_ONLY_PREFLIGHT", "PREFLIGHT_RESULT_AUDIT", "EXECUTION_CONTRACT_AUDIT", "EXECUTION_IMPLEMENTATION_AUDIT", "PROTOCOL_LOCK", "MAPPER_FIT_GATE", "PROFILE_RATING_OPEN", "ALL_HEAD_RANK_SEAL", "EVALUATION_LABEL_OPEN", "METRICS_BOOTSTRAP_RESULT_SEAL", "RESULT_AUDIT"], "phase order drift")
    require(contract["resume"] == {"required_after_lock": True, "reuse_requires": ["CONTRACT_HASH", "SOURCE_HASHES", "IMPLEMENTATION_HASHES", "REGISTRY_HASH", "MEMBERSHIP_HASH", "MAPPER_HASH"], "partial_or_drift": "FAIL_CLOSED_NO_OVERWRITE", "post_label_change_requires_new_evidence_id": ["SALT", "COHORT", "ALPHA", "SEED", "CELL", "CONTRAST"]}, "resume/post-label retune drift")
    require(contract["claim_boundary"]["allowed"] == "ADAPTIVE_MECHANISM_SCREEN_WITH_USER_MOVIE_PAIR_NONREUSE_SINCE_REC_EV_019A_ONLY", "claim literal drift")
    require(set(contract["claim_boundary"]["forbidden"]) == {"PROJECT_HISTORY_WIDE_FRESHNESS", "CONFIRMATORY_EVIDENCE", "USER_INDEPENDENCE", "ITEM_INDEPENDENCE", "NEW_OR_UNRATED_MOVIE_GENERALIZATION", "KOREAN_USER_PERFORMANCE", "POST_SNAPSHOT_PERFORMANCE", "CHAMPION_OR_PRODUCT_POLICY"}, "claim boundary drift")
    require(contract["stop_rule"] == "IF_ANY_PRELABEL_FLOOR_OR_MAPPER_FIT_GATE_FAILS_STOP_WITHOUT_OPENING_PROFILE_OR_EVALUATION_RATINGS; AFTER_LABEL_OPEN_REPORT_FROZEN_RESULT_WITHOUT_RELAXATION; NO_FURTHER_OFFLINE_CONFIRMATORY_CLAIM_WITHOUT_A_NEW_UNEXPOSED_POPULATION", "stop rule drift")
    require(canonical_sha256(contract) == EXPECTED_CANONICAL_SHA256, "canonical contract hash drift")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    args = parser.parse_args()
    require(args.contract.resolve() == DEFAULT.resolve(), "only the committed default design is accepted")
    validate(load(args.contract))
    print("REC_EV_026_DESIGN_CONTRACT_VALID")


if __name__ == "__main__":
    main()
