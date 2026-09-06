"""Fail closed on the REC-EV-027 strict item-cold design contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "docs/recommendation/contracts/rec-ev-027-strict-item-cold-model-screen.json"


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


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
    require(sha256_file(path) == str(spec["sha256"]), f"hash drift: {path}")


def verify_catalog_artifacts(contract: dict[str, Any]) -> None:
    catalog = contract["catalog_content_build"]
    pins = catalog["artifact_pins"]
    expected = set(catalog["outputs"])
    require(isinstance(pins, dict) and set(pins) == expected, "catalog artifact pin inventory incomplete")
    for key, spec in pins.items():
        require(spec["path"] == catalog["outputs"][key], f"catalog artifact path drift: {key}")
        verify_artifact(spec)
    manifest_path = resolve(catalog["outputs"]["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    build_contract = contract["allowed_input_artifacts"]["catalog_build_contract"]
    require(
        manifest["catalog_build_contract_sha256"] == build_contract["sha256"],
        "catalog manifest build-contract hash drift",
    )
    build_contract_body = json.loads(resolve(build_contract["path"]).read_text(encoding="utf-8"))
    require(
        manifest["implementation_sha256"] == build_contract_body["implementation"]["sha256"],
        "catalog manifest implementation hash drift",
    )
    expected_implementations = [
        {key: build_contract_body["implementation"][key] for key in ("path", "bytes", "sha256")},
        *build_contract_body["implementation"]["transitive_helpers"],
    ]
    require(manifest["implementation_artifacts"] == expected_implementations, "catalog helper provenance drift")
    inner = {row["path"]: row for row in manifest["artifacts"]}
    for key in ("identity", "structured", "embeddings", "domain_projection", "summary"):
        external = pins[key]
        require(inner.get(external["path"]) == external, f"catalog manifest pin drift: {key}")
    summary = json.loads(resolve(catalog["outputs"]["summary"]).read_text(encoding="utf-8"))
    require(summary["status"] == "PASS_RATING_INDEPENDENT_FULL_CATALOG_BUILD", "catalog summary did not pass")
    require(summary["ratings_member_opened"] is False, "catalog summary opened ratings")
    require(summary["rating_values_opened"] is False, "catalog summary opened rating values")
    require(summary["locked_test_opened"] is False and summary["final_reserve_opened"] is False, "reserved labels opened")


def validate(contract: dict[str, Any], *, verify_files: bool = True) -> None:
    expected_top = {
        "schema_version", "contract_id", "status", "purpose", "question", "design_audit", "claim_boundary",
        "authorization", "research_basis", "allowed_input_artifacts", "forbidden_input_artifacts",
        "catalog_content_build", "source_population", "item_universe", "strict_item_firewall", "tracks", "membership", "profile_policy",
        "evaluation_labels", "models", "common_scoring", "screen_and_replication", "metrics", "statistics",
        "execution_phases", "output_root",
    }
    require(set(contract) == expected_top, "top-level contract shape drift")
    require(contract["schema_version"] == 1, "schema drift")
    require(contract["contract_id"] == "rec-ev-027-strict-item-cold-model-screen-v1", "id drift")
    require(contract["status"] in {
        "PROPOSED_FOR_INDEPENDENT_DESIGN_AUDIT", "APPROVED_FOR_ADAPTIVE_STRICT_ITEM_COLD_EXECUTION"
    }, "status drift")
    approved = contract["status"] == "APPROVED_FOR_ADAPTIVE_STRICT_ITEM_COLD_EXECUTION"

    auth = contract["authorization"]
    for key in ("locked_test_access", "final_reserve_access", "timestamp_access", "champion_selection", "product_policy_change"):
        require(auth[key] is False, f"forbidden authorization enabled: {key}")
    require(auth["rating_value_access_before_exact_contract_pass"] is False, "pre-audit label access enabled")
    require(auth["rating_independent_catalog_build_before_exact_audit"] is True, "catalog prerequisite disabled")

    sources = contract["allowed_input_artifacts"]
    require(set(sources) == {
        "movielens_archive", "catalog_build_contract", "structured_feature_helper",
    }, "source inventory drift")
    if verify_files:
        for spec in sources.values():
            verify_artifact(spec)
    forbidden = set(contract["forbidden_input_artifacts"])
    require(any("global-time-v1/test.parquet" in item for item in forbidden), "Locked Test path not forbidden")
    require(any("final-reserve" in item for item in forbidden), "final reserve path not forbidden")

    catalog = contract["catalog_content_build"]
    require(catalog["allowed_archive_members_before_exact_audit"] == ["ml-32m/movies.csv", "ml-32m/links.csv"], "catalog members drift")
    require(catalog["ratings_member_access"] is False, "catalog build may open ratings")
    require(catalog["expected_catalog_movies"] == 87585 and catalog["expected_positive_tmdb_links"] == 87461, "catalog counts drift")
    require(catalog["every_catalog_movie_attempted"] is True, "partial catalog build enabled")
    require("EVERY_CATALOG_ID_RUNS_THE_SAME" in catalog["tmdb_cache_reuse"] and "CACHE_MEMBERSHIP_NEVER_DEFINES_PROCESSING" in catalog["tmdb_cache_reuse"], "cache membership leak")
    require("EVERY_ELIGIBLE_ROW_REBUILDS" in catalog["embedding_reuse"] and "INPUT_TEXT_SHA256" in catalog["embedding_reuse"], "embedding cache reuse guard missing")
    require("PRODUCTION_COUNTRIES" in catalog["korean_origin_proxy"], "Korean proxy drift")
    pins_present = isinstance(catalog["artifact_pins"], dict)
    if approved:
        require(contract["design_audit"].get("latest_pass") == "REC_EV_027_DESIGN_PASS_EXACT_CONTRACT", "exact audit pass missing")
        require(pins_present, "approved contract lacks catalog pins")
    if pins_present:
        if verify_files:
            verify_catalog_artifacts(contract)
        else:
            require(set(catalog["artifact_pins"]) == set(catalog["outputs"]), "catalog artifact pin inventory incomplete")
    else:
        require(not approved, "approved contract lacks catalog pins")

    population = contract["source_population"]
    require(population["allowed_old_buckets_inclusive"] == [0, 59], "old-bucket allowlist drift")
    require(population["excluded_old_buckets_inclusive"] == [60, 99], "old-bucket exclusion drift")
    require(population["model_train_role_inclusive"] == [0, 5999], "model-train role drift")
    require(population["evaluation_role_inclusive"] == [6000, 7999], "evaluation role drift")
    require(population["forbidden_roles_inclusive"] == [8000, 9999], "forbidden role drift")
    require(population["evaluation_phase_salt"] == "rec-ev-027-phase-user-v1", "evaluation phase salt drift")
    require(population["screen_evaluation_phase_inclusive"] == [0, 1999], "screen phase drift")
    require(population["replication_and_transfer_phase_inclusive"] == [2000, 9999], "replication phase drift")
    require(population["phase_overlap"] == 0, "evaluation phases overlap")
    require(population["excluded_reader_policy"] == "PARSE_USER_ID_ONLY_THEN_DISCARD_RAW_LINE", "reader firewall drift")

    universe = contract["item_universe"]
    require("RATING_INDEPENDENT_87585_MOVIE_CATALOG" in universe["source"], "rating-independent universe missing")
    require("INTERACTION_COUNT_FILTER" in universe["source"], "interaction-count independence missing")
    require("build_structured_full" in universe["structured_transform"], "structured transform helper missing")
    require("NO RATING POPULARITY OR ITEM IDENTITY FEATURE" in universe["structured_transform"], "structured transform leaked behavior")
    forbidden_membership = set(universe["forbidden_membership_inputs"])
    require({"RATING", "RATING_COUNT", "POPULARITY", "TIMESTAMP", "BPR_FACTOR"} <= forbidden_membership, "item universe may use behavior")

    firewall = contract["strict_item_firewall"]
    require(firewall["replication_folds"] == [1, 2, 3, 4] and firewall["screen_fold"] == 0, "fold roles drift")
    require(set(firewall["forbidden_cold_item_access"]) >= {
        "RATING_VALUE_BEFORE_RANK_SEAL", "INTERACTION_COUNT", "POPULARITY", "ITEM_ID_FEATURE",
        "LEARNED_ITEM_FACTOR", "COOCCURRENCE",
    }, "cold firewall incomplete")
    require(set(firewall["assertions"]) >= {
        "NO_COLD_MOVIE_ID_IN_ANY_TRAIN_INTERACTION_MATRIX",
        "NO_COLD_ITEM_IDENTITY_FEATURE",
        "NO_COLD_ITEM_TEACHER_FACTOR_READ",
        "ALL_PRIMARY_AND_BINARY_SENSITIVITY_TARGET_RANKS_FOR_A_PHASE_SEALED_BEFORE_ANY_TARGET_RATING_VALUES_FOR_THAT_PHASE_ARE_PARSED",
    }, "cold assertions incomplete")

    tracks = contract["tracks"]
    require(set(tracks) == {"RANDOM_ITEM_COLD", "KOREAN_ORIGIN_COLD", "RELEASE_2020_2023_COLD"}, "track inventory drift")
    for track in tracks.values():
        require(int(track["profile_n"]) == 8, "profile K drift")
        require(int(track["target_n"]) >= 4 and int(track["target_n"]) % 2 == 0, "target slate must be even")

    membership = contract["membership"]
    require(membership["rating_value_and_timestamp_access"] is False, "membership may parse labels")
    require("USER_ID_AND_MOVIE_ID_ONLY" in membership["evaluation_interaction_membership_exception"], "rated-slate exception unclear")
    require({"RATING_VALUE", "Q", "TIMESTAMP", "POPULARITY", "MODEL_SCORE"} <= set(membership["selection_forbidden"]), "membership selection leak")
    require(membership["profile_target_disjoint"] is True, "profile/target overlap enabled")

    profile = contract["profile_policy"]
    require(profile["primary"] == "PERCENTILE_MAGNITUDE" and profile["k"] == 8, "primary input drift")
    require(profile["rating_bins"] == [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0], "rating bins drift")
    require("WARM" in profile["prior_population"] and "EQUAL_USER_MEAN" in profile["prior_population"], "fold-specific prior not exact")
    require(profile["q_profile_formula"].endswith("DIV_13"), "K8 tau5 denominator drift")
    require("SCORES_AND_RANKS_SEALED" in profile["sensitivity"] and "BEFORE_ANY_PHASE_TARGET_LABEL" in profile["sensitivity"], "sensitivity leak risk")
    require("NOT_AN_OPTIMALITY_CLAIM" in profile["selection_status"], "K8 claim boundary missing")

    labels = contract["evaluation_labels"]
    require(labels["output_n"] == 2, "Top-2 drift")
    require(labels["unrated_semantics"] == "UNKNOWN_NOT_NEGATIVE", "unrated semantics drift")
    require("AFTER_ALL_MODEL_SCORE_AND_RANK_ARTIFACTS" in labels["target_rating_access"], "label firewall drift")
    require(labels["q_eval_formula"].endswith("DIV_N_FULL"), "q_eval formula drift")

    models = contract["models"]
    require(set(models) == {
        "RANDOM_EXPECTATION", "STRUCTURED_DIRECT", "E5_DIRECT", "STRUCTURED_E5_RRF",
        "FEATURE_ONLY_LIGHTFM", "E5_TO_WARM_BPR_RIDGE", "E5_EPISODIC_CONTENT",
    }, "model inventory drift")
    require(models["FEATURE_ONLY_LIGHTFM"]["item_identity_features"] is False, "LightFM item IDs enabled")
    lightfm_dependency = models["FEATURE_ONLY_LIGHTFM"]["dependency"]
    require({key: lightfm_dependency[key] for key in (
        "distribution", "version", "python", "platform", "learning_schedule", "runtime_image"
    )} == {
        "distribution": "lightfm-next", "version": "1.19.0", "python": "3.12",
        "platform": "linux_amd64", "learning_schedule": "adagrad",
        "runtime_image": "python:3.12.5-slim-bookworm@sha256:c24c34b502635f1f7c4e99dc09a2cbd85d480b7dcfd077198c6b5af138906390",
    }, "LightFM dependency drift")
    if verify_files:
        verify_artifact(lightfm_dependency["lockfile"])
    require("NO_SEPARATE_SAMPLE_WEIGHT" in models["FEATURE_ONLY_LIGHTFM"]["loss"], "LightFM signed semantics ambiguous")
    require("FOLD_IN_USER_VECTOR" in models["FEATURE_ONLY_LIGHTFM"]["candidate_score"], "LightFM evaluation scoring missing")
    require("RANDOM_STATE=np.random.RandomState(SEED)" in models["FEATURE_ONLY_LIGHTFM"]["index_and_rng"], "LightFM RNG mapping missing")
    require("COO SORT USER_ROW_ASC_THEN_ITEM_ROW_ASC" in models["FEATURE_ONLY_LIGHTFM"]["index_and_rng"], "LightFM COO order missing")
    require("NO_ITEM_IDENTITY_FEATURES" in models["FEATURE_ONLY_LIGHTFM"]["index_and_rng"], "LightFM item identity enabled")
    require(models["E5_TO_WARM_BPR_RIDGE"]["teacher_seeds"] == [17, 42, 73], "BPR seed drift")
    require("NO_BATCH_SHUFFLE" in models["E5_TO_WARM_BPR_RIDGE"]["teacher_optimizer"], "BPR ordering ambiguous")
    require("WITHIN_USER_FLAT_INDEX_ASC" in models["E5_TO_WARM_BPR_RIDGE"]["teacher_optimizer"], "BPR optimizer pair order drift")
    require("PAIR_HASH_ASC" not in models["E5_TO_WARM_BPR_RIDGE"]["teacher_optimizer"], "obsolete BPR pair order present")
    require("GRAD_U=" in models["E5_TO_WARM_BPR_RIDGE"]["teacher_optimizer"], "BPR gradient equation missing")
    require("REC_EV_027_BPR_PAIR_V2" in models["E5_TO_WARM_BPR_RIDGE"]["teacher_pairs"], "BPR pair sampler salt missing")
    require("0x9E3779B97F4A7C15" in models["E5_TO_WARM_BPR_RIDGE"]["teacher_pairs"], "BPR pair sampler recurrence missing")
    require("FLAT_INDEX_ASC" in models["E5_TO_WARM_BPR_RIDGE"]["teacher_pairs"], "BPR pair order missing")
    require("PAIR_TOUCHED_ITEMS_ONLY" in models["E5_TO_WARM_BPR_RIDGE"]["mapper"], "untouched BPR factors may enter mapper")
    require("OUTER_TRACK_FOLD" in models["E5_TO_WARM_BPR_RIDGE"]["inner_item_split"], "ridge inner split missing")
    require("NP_LINALG_SOLVE" in models["E5_TO_WARM_BPR_RIDGE"]["mapper"], "ridge solve not exact")
    require("AT_LEAST_1000" in models["E5_TO_WARM_BPR_RIDGE"]["inner_support"], "ridge support guard missing")
    require("PAIR_TOUCHED" in models["E5_TO_WARM_BPR_RIDGE"]["inner_support"], "ridge inner support includes untouched items")
    require("ALL AND ONLY" in models["E5_TO_WARM_BPR_RIDGE"]["mapper_refit"] and "PAIR_TOUCHED" in models["E5_TO_WARM_BPR_RIDGE"]["mapper_refit"], "ridge refit includes untouched items")
    require("AVERAGE_THREE_SEED_SCORES_BEFORE_RANK" in models["E5_TO_WARM_BPR_RIDGE"]["evaluation_score"], "ridge evaluation score missing")
    require(models["E5_EPISODIC_CONTENT"]["teacher"] is None, "episodic model unexpectedly uses CF teacher")
    require("DISJOINT" in models["E5_EPISODIC_CONTENT"]["episode_eligibility"], "episodic split overlap")
    require("EPISODE_POSITIVE_V1" in models["E5_EPISODIC_CONTENT"]["episode_selection"], "episodic positive salt missing")
    require("EPISODE_NEGATIVE_V1" in models["E5_EPISODIC_CONTENT"]["episode_selection"], "episodic negative salt missing")
    require("NO SHUFFLE" in models["E5_EPISODIC_CONTENT"]["deterministic_execution"], "episodic batch order missing")
    require("TORCH_MANUAL_SEED" in models["E5_EPISODIC_CONTENT"]["deterministic_execution"], "episodic initialization missing")
    require(models["E5_EPISODIC_CONTENT"]["parameters"]["torch_deterministic_algorithms"] is True, "episodic determinism disabled")

    scoring = contract["common_scoring"]
    require("SYSTEM_CELL" in scoring["systems_not_algorithms"], "system-cell claim missing")
    require(scoring["inactive"].startswith("NO_RANK"), "inactive behavior drift")
    require(scoring["tie_order"].startswith("SCORE_DESC"), "tie rule drift")

    metrics = contract["metrics"]
    require(list(metrics["primary"]) == ["HARM20", "TOP2_MEAN_Q", "TOP2_MIN_Q"], "metric priority drift")
    require(metrics["unit"] == "EVALUATION_USER", "statistical unit drift")
    require("UNRATED_ITEMS_ARE_NOT_RELEVANCE_ZERO" in metrics["forbidden_catalog_metric_interpretation"], "unrated catalog guard missing")

    screen = contract["screen_and_replication"]
    require(screen["advance_cap"] == 2, "advance cap drift")
    require(screen["selection_effect"].endswith("NEVER_POINT_ESTIMATE"), "selection effect ambiguous")
    require(screen["reporting_order"] == screen["screen_models"], "screen reporting order drift")
    require(screen["empty_direct"].startswith("IF_NO_DIRECT_SYSTEM_HAS_ACTIVE_RATE_GTE_0_95_STOP"), "empty direct branch missing")
    require(screen["replication"].startswith("Refit only"), "replication refit requirement missing")
    require("No hyperparameter retuning" in screen["replication"], "replication retuning enabled")
    stats = contract["statistics"]
    require(stats["confidence"] == 0.95 and stats["screen_repeats"] == 5000, "screen interval drift")
    require("45 PREDECLARED CONTRASTS" in stats["screen_contrast_family"], "screen family incomplete")
    require(stats["no_harm_gate"].endswith("GTE_0"), "no-harm gate drift")
    require(stats["active_rate_gate"] == 0.95, "active-rate gate drift")
    require(stats["bootstrap_runtime"]["critical_quantile_method"] == "HIGHER", "bootstrap quantile drift")
    require(all(track in stats["transfer_contrast_family"] for track in (
        "KOREAN_ORIGIN_COLD", "RELEASE_2020_2023_COLD"
    )), "transfer family incomplete")
    require(set(stats["transfer_system_truth_table"]) == {
        "PASS_PROXY_SIGNAL", "FAIL_PROXY_SIGNAL", "INCONCLUSIVE_PROXY_SIGNAL"
    }, "transfer system truth table drift")
    require(set(stats["transfer_track_truth_table"]) == {
        "PROXY_SIGNAL", "NO_PROXY_SIGNAL", "INCONCLUSIVE_PROXY_SIGNAL"
    }, "transfer track truth table drift")
    require(stats["replication_repeats"] == 5000 and stats["replication_seed"] == 20261002, "replication bootstrap drift")
    require(stats["transfer_repeats"] == 5000 and stats["transfer_seed"] == 20261003, "transfer bootstrap drift")
    require("ONE USER_KEY_ASC UNION" in stats["replication_pooled_estimator"], "replication bootstrap universe ambiguous")
    require("DRAWS EXACTLY UNION_USER_COUNT" in stats["replication_pooled_estimator"], "replication sample size ambiguous")
    require("MAX_ABS_T" in stats["replication_pooled_estimator"], "replication max-t rule incomplete")
    require("EACH OF FOLDS1_TO4" in stats["replication_active_gate"], "replication active gate incomplete")
    require("AT_LEAST_THREE_OF_FOUR_FOLDS" in stats["replication_fold_direction"], "replication fold rule incomplete")
    require(set(stats["replication_system_truth_table"]) == {
        "PASS_RANDOM_REPLICATION", "FAIL_RANDOM_REPLICATION", "INCONCLUSIVE_RANDOM_REPLICATION",
        "PASS_DIRECT_INCREMENT", "FAIL_DIRECT_INCREMENT", "INCONCLUSIVE_DIRECT_INCREMENT",
    }, "replication system truth table drift")
    require(set(stats["replication_track_truth_table"]) == {
        "LEARNED_INCREMENT_REPLICATED", "CONTENT_SIGNAL_WITHOUT_LEARNED_INCREMENT",
        "NO_REPLICATION_SIGNAL", "INCONCLUSIVE_REPLICATION",
    }, "replication track truth table drift")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT)
    parser.add_argument("--skip-file-hashes", action="store_true")
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    validate(contract, verify_files=not args.skip_file_hashes)
    print(json.dumps({"status": "PASS_REC_EV_027_CONTRACT", "contract": str(args.contract)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
